## session port-68: TMs work now, and the Elite Four economy is understood

**The TM path was broken and is fixed, which unblocked real movesets.**
`teach()` learned the move and then left `sLockFieldControls = 1` forever, so
every later movement press was eaten and any savestate written afterwards was
unusable. Four rounds of button-mashing could not clear it; a **screenshot**
settled it in one look: the stuck frame is the BATTLE MOVES list with the
cursor on SURF and the description box reading **"HM moves can't be forgotten
now."** It was never waiting for a menu to close -- it was waiting to be told
to give up.

Cancelling that costs three presses in order, all long: **B** asks "give up
trying to learn X?", **A** answers YES, a further **B** leaves the bag.
Measured on a wedged teach:

    24 B presses across three hold lengths  -> lock still 1
    20 A presses                            -> lock still 1
    direct DOWN/LEFT/RIGHT                  -> player never moved
    B/A/B with 16-frame holds               -> cleared in 7 rounds

Verified after the fix: `teach -> True`, lock 0, scene inactive, the player
walks, the move persists. **EMBER (Blaziken) now has OVERHEAT (140, Fire
STAB)** -- and three of the four planned teaches were correctly REFUSED
rather than forced, which is the heuristic working.

**A move's price is what it can DELIVER.** SPIT UP reads 100 power in the ROM
table and does nothing without Stockpile -- ten measured turns of a L100
PELIPPER hitting a WALREIN for 0. The teacher was pricing that at 100 and
refusing every TM to protect it. `effective_power()` now zeroes conditional
moves and feeds all three rankings; wiring only two of the three let
consecutive TMs overwrite EACH OTHER.

**The Elite Four economy, measured rather than assumed.** Each leader pays as
they fall, so the run reaches Drake holding ~16,000 -- and the mart is four
maps behind it. A whiteout then takes half.

There is **no mid-gauntlet resupply**, and the reason is in the ROM, not our
routing. `elite_four.inc:24-29`:

    setmetatile 5,12 / 6,12 / 7,12  EntryDoor_ClosedTop,    1
    setmetatile 5,13 / 6,13 / 7,13  EntryDoor_ClosedBottom, 1

Walking into a leader's room turns all six tiles of the way back impassable at
runtime. The static grid still reads them walkable, which is exactly why every
walk-out attempt stopped at **(7,11)** -- one row short of a door that no
longer exists. Twice in Drake's room, once in Glacia's, each time with the
whole column reading collision 0 and no object in the way. `sync_grid()` now
runs after each fight and reports **18 changed cells per room**, so nav stops
planning through them. That is the fourth time this session a SCRIPT was
holding the player while the decoded grid said "walkable" -- and the first
time the tool built for it (`grid_drift`/`sync_grid`) was actually pointed at
the problem.

**Where the fight stands.** Sidney, Phoebe and Glacia fall reliably; Drake
wipes the party. He is five dragons, and the bag has no Ice TM -- OVERHEAT is
0.5x into every one of them and SURF only reaches 2x on Flygon. So there is no
type answer available tonight, only levels and healing, and both are
accumulating unattended:

    NINJA 54->57   LOTTAD 48->51   EMBER 54->59   ROCKY 51->56   MIGHTYENA 54->60

Steven himself is read from the ROM and is beatable by two mons once they
arrive: Skarmory L57 / Claydol L55 / Aggron L56 / Cradily / Armaldo /
Metagross, where **SURF is 4x on Aggron** and **OVERHEAT 4x on Skarmory**,
2x on three more between them.

## session port-67: the Elite Four is now a training ground, not a wall

Four deadlocks fixed, all of them found from the SCREEN rather than from a
counter -- twice reported from the couch before anything in the harness
noticed. Worth grouping, because they are one family: **a script was holding
the player and the decoded grid said "walkable" every time.**

1. **"Stuck flipping through the pokemon selection menu."** A real deadlock,
   and the precise shape was:

       tactics wants ('switch', 0)  -> retired this battle
         -> fallback 'flee'         -> "cannot run from a trainer battle"
         -> 'flee' retired
         -> tactics still wants ('switch', 0) -> retired -> 'flee' -> ...

   `_live_alternative` promises in its own docstring that "returning the
   retired action again is the one thing this must never do", and it had TWO
   unguarded `return "flee"` exits doing exactly that. Both are now gated on
   flight still being live, and when everything is dead it attacks anyway: a
   move with no PP is what the engine turns into **Struggle**, so the turn
   always resolves. Five tests pin it -- and they earned their keep by
   catching the second exit that the first patch missed.

2. **Pinned at Glacia's feet for half an hour**, one room from Drake, having
   already beaten three leaders. An NPC you are still FACING re-enters
   dialogue on the next A, so every press meant for the exit warp re-opened
   her victory speech. One interleaved step breaks it.

3. **Elite Four rooms SEAL themselves.** `EverGrandeCity_DrakesRoom_
   EventScript_WalkInCloseDoor` does `lockall` and calls
   `PokemonLeague_EliteFour_EventScript_WalkInCloseDoor`. That is why a
   "retreat to the hall" idea could never work: from Drake's (6,7) every
   approach to the (6,13) door stalled at (7,11) while the whole column read
   collision 0, elevation 3, with no object in the way. The only exits are
   winning or a whiteout -- so the loop engages the trainer instead. And a
   room whose leader is ALREADY beaten is a third case the first fix missed:
   talking is then just conversation, no battle, no whiteout, so it walks out
   through the doors the script has reopened.

4. **A forced replacement was failing the whole battle.** The engine does not
   always honour the slot we nominate, and `_forced_switch` returned False for
   that -- which `play()` reads as "stuck" and abandons the fight. Gauntlets
   that were going fine were thrown away over a mon we had not picked. It now
   asks the question that matters: is a living mon standing there?

**Then the strategy changed, and this is the part that matters.** SEA BIRD is
L100, so every Elite Four mon it knocks out is experience thrown away -- while
the five members that cannot survive the gauntlet are exactly the ones that
need it. Gen 3 splits experience between PARTICIPANTS, so `--train` fronts the
laggard.

Fronting it alone was worse: it died on the spot and the lap ended two battles
deep, costing more experience than it earned (five logged attempts, each dying
at Phoebe or Glacia). So it now switches **in for one turn and back out** --
participation is all the engine asks for. Measured over half an hour:

    LOTTAD 48->49   EMBER 54->55   ROCKY 51->53   MIGHTYENA 54->56

**+7 bench levels in thirty minutes**, against about one level per seven
minutes for wild grinding on Victory Road 1F.

**And the target is known now, not guessed.** Steven is Skarmory L57, Claydol
L55, Aggron L56, Cradily, Armaldo, Metagross. Surf hits **Aggron 4x**, Claydol
and Armaldo 2x, so the lead can win it alone once it arrives intact. The bench
only has to soak enough of Drake to get it there -- which is precisely what
these levels buy.

The loop is self-funding (a whiteout returns to the League nurse healed,
keeping ~8,000 prize money, which buys six Hyper Potions) and runs unattended.

## session port-66: the lead's PP was the wall, and a TM teach poisons the save

**Why Steven kept winning.** SEA BIRD (L100) carries the whole gauntlet, and
its moveset was SURF (15 PP) / SPIT UP (10) / FLY (15) / HYDRO PUMP (5). SPIT
UP reads **100 power** in the ROM's table and deals literally nothing without
Stockpile -- measured, ten consecutive turns hitting a WALREIN for 0 while the
clock ran out. So the real budget was ~35 PP of damage for five battles, and
the lead arrived at the Champion Struggling.

**The teacher was protecting the useless move.** `Teacher` priced the
sacrifice by ROM base power, so every TM was refused with "would overwrite
SPIT UP (100 power); no party member has a spare or cheap slot to spare".
`effective_power()` now zeroes moves whose listed power needs setup this run
never does, and it feeds all three rankings: the candidate sort, the
pre-flight refusal price, and the engine's own which-move-to-forget prompt.
Wiring only the first two was not enough -- two TMs in a row then overwrote
EACH OTHER while SPIT UP sat untouched. Six tests pin it, including that an
unreadable move is priced FREE rather than priceless (the old code answered
999, the same refuse-everything bug by another route).

Result: SPIT UP -> **AERIAL ACE (20 PP, never misses)**, four damaging moves,
55 PP. TM47 STEEL WING was then correctly refused because it would have cost
AERIAL ACE -- the heuristic working as intended.

**But `teach()` leaves the field LOCKED, and that poisons the savestate.**
It logs "still on a menu after backing out", returns True (the move really is
learned), and the game never gets control back. Proven exhaustively: 30
`close_menus()` + B, then 10x `B:8`, 6x `A:8`, 8x `B:16` -- `scene_active()`
stayed True, and pressing DOWN/LEFT/RIGHT **directly** (bypassing `step_dir`,
whose own scene check made my first "movement proof" circular) never moved the
player. Any state saved after a teach is unusable: `armed.state` pinned the
next run at (3,4) for four minutes before StallWatch called it.

So teaching must be treated as a one-way trip for now: do it, use the result
in the same process, and never save afterwards. Recorded rather than papered
over -- the honest fix is inside `Teacher._back_out`, which cannot be written
blind without a screen reader.

**Two loop bugs found by running it, both mine:**
- `into_hall` only knew the hall and the plateau, so a run starting inside
  Victory Road exited 1 and `restart=on-failure` relaunched it **33 times in
  nine seconds**. It now takes the (39,5) door out.
- I pre-walked to "below the door" at (39,6), which is **WALL** -- the warp is
  entered from (38,5)/(40,5) and `take_warp` routes itself. goto had no path
  and sat pinned at (22,31) for twenty-six minutes with the frame counter
  climbing. `league_loop.py` now arms StallWatch, which is what finally named
  it.

**The economy that makes this winnable.** A whiteout up here returns the
player to the League nurse -- healed, beside the mart, with no dungeon to
re-cross -- and the four members we can already beat pay prize money: one
attempt took the run ¥545 -> ¥10,672. `scripts/league_loop.py` turns that
into a cycle: heal free, spend everything on healing items, fight, and on a
loss come back richer. Each pass also banks experience off L46-55 opponents,
worth far more than the wild grind on 1F (about one level per seven minutes).

## session port-65: ELITE FOUR -- four of five beaten, Steven stands

Victory Road is crossed (port-64) and the League is now reachable end to end
by one command. `scripts/elite_four.py` walks the eleven-map gauntlet and has
beaten **Sidney, Phoebe, Glacia and Drake**. Steven is not down yet.

**The hall changes the problem.** `EverGrandeCity_PokemonLeague` has a NURSE
at (3,2) and a MART at (16,2) -- the only ones reachable from the upper
plateau, since Ever Grande's other Center is at (27,48) on the lower one and
row 37 of the city is solid wall. `d.heal()` does NOT find her (it looks the
nurse up through the sprite table and answers False), but she is an ordinary
NPC and `talk_to(3,2)` restores the whole party's HP **and PP**. The mart
stocks HYPER POTION / MAX POTION / FULL RESTORE / REVIVE, which is what made
Drake winnable.

**The guards stand ON the doorway.** (9,2) and (10,2) are occupied by the two
badge-checkers, so the door at (9,1) is unreachable until one is spoken to;
they then step aside to (8,2)/(11,2). Five "exit attempt" lines and no door.

**A whiteout costs nothing up here.** It returns the player to the last Center
used -- the League nurse -- so a lost gauntlet lands back on the plateau,
healed, with the mart next door and no dungeon to re-cross.

Two real fixes, both found by the fights:

1. **A party menu that survives B presses IS a forced replacement.**
   `gUnknown_02038473 == 1` is the engine's own "send this one out" flag and it
   is right when set, but it is not set for every replacement the engine
   demands. When a mon faints mid-turn the menu cannot be backed out of, so the
   "stale menu, press B" branch pressed B against an immovable menu while the
   opponent attacked for free -- ten consecutive lines, then "sent out party
   slot 1 but gBattlerPartyIndexes is [0]". Now self-correcting: two B presses
   and it acts. Five unit tests pin both directions.
2. **Runners publish to the feed the WIDGET reads.** A Driver auto-attaches a
   feed named after its STATE FILE, so a run on `saves/gauntlet2.state` wrote
   `live/gauntlet2.png` while the desktop watched `live/default.*` and showed a
   frame from 102 minutes earlier -- reported, correctly, as "last frame was
   over 6000 seconds ago" while the game was running perfectly. The emulator
   allows exactly one tick observer, so `--feed` must REPLACE it.

**And one mistake of mine, worth recording because it was strictly harmful.**
I replaced the harness's battle policy with a hand-rolled one that read PP off
`frame["moves"]` -- which does not carry the live counts. It kept asking for
HYDRO PUMP after its 5 PP were spent, the harness fell back to SPIT UP (no
damage without Stockpile), and the level-100 lead lost 158 HP to a Walrein it
never scratched over ten turns. `tactics.recommend` already ranks a certain KO
first, then healing, then a status cure, then a resist-switch, then best
expected damage, and it reads PP and the live accuracy stages properly. The
only thing worth changing for a five-battle gauntlet is WHEN it heals: 0.35 is
one turn from dead against a level-55 dragon.

**What actually blocks Steven: party depth, not tactics.** SEA BIRD (L100)
carries every battle until its PP runs out; the other five are L48-54 against
a L46-55 Elite Four, so each is a free KO that feeds the next.
`scripts/plateau_train.py` trains them without re-crossing the dungeon: the
upper plateau has its OWN Victory Road door at (18,27), landing on 1F(39,5)
inside the goal component, which has L40-45 grass two warps from the nurse.

Its first version identified the laggard, logged "training LOTTAD L48", and
then let the level-100 lead take every battle -- LOTTAD gained nothing across
three rounds. Gen 3 splits experience between PARTICIPANTS, so the switch IS
the mechanism; `balance.py` already knew that, including that the
"already switched" flag must be per BATTLE and not per run. Wired, verified
live (`T1 switch:4 ROCKY#4`), and ROCKY is climbing.

Milestones: `at-league.state`, `league-entry.state`, `e4stocked2.state`,
`champion4.state`, `train.state`.

## session port-64: VICTORY ROAD IS CROSSED -- the answer was SURF

**`EverGrandeCity_PokemonLeague (9,11)`**, banked as `saves/at-league.state`.
Six sessions were stuck on this dungeon. The blocker was never geometry.

**Victory Road B2F holds a 256-cell lake of `MB_OCEAN_WATER` (0x15) at
elevation 1, and every reachability fill this project ever ran defaulted to
`surfing=False`** -- so the lake decoded as void, the arrival pocket read as a
sealed 65-cell dead end, and each of its exits looked like an elevation seam
(z=15 bridge -> z=4 grass, arriving with z=3). With surf enabled the fill from
B2F(30,25) goes **65 -> 634 cells** and includes both (19,12) and (43,2);
`find_path` answers with a 71-move route. Nothing about the map was ever
wrong. The question was.

Worth being blunt about how much work went into proving the wrong thing: ROM
warp tables read directly out of the binary, raw `gBackupMapLayout` blocks
decoded by hand, `MB_IMPASSABLE_NORTH` counted per floor, eight component
links tested at state level, nine boundary steps driven live against the
engine, and all four maps rendered as ASCII. Every one of those measurements
was correct and none of them found it, because they all shared one hidden
assumption. **A negative result that keeps agreeing with itself is evidence
about the method, not the map.**

The full route, all verified live:

    EverGrande(18,41) -> 1F(15,40) -> 1F(9,14) -> B1F(8,3)
      -> B1F(30,25) -> B2F(30,25)
      -> walk to the SHORE at B2F(33,17), mount Surf facing U
      -> surf the lake to B2F(19,12) -> B1F(17,16)     [goal component]
      -> Sokoban: shove the boulder off (20,26), ROCK SMASH (21,26),
         east along row 26 to (22,26), north up the x=22 corridor
      -> B1F(20,21) -> 1F(21,32) -> 1F(39,5)
      -> EverGrandeCity(18,28)  [the UPPER plateau]
      -> (18,5) -> EverGrandeCity_PokemonLeague

Four executor bugs fixed getting it to run end to end:
1. **Mount before planning.** `nav.surfing` gates whether water is road, so a
   plan made on foot routes around the only way through. And a mount needs the
   FACED tile to be water -- it cannot be done from the arrival cell, which is
   a dozen cells from the shore ("could not mount surf" was all it said).
2. **B1F appears twice in the route.** A leg list keyed on map name sent the
   return trip back down the stairs it had just climbed; legs now locate
   themselves by COMPONENT (is (20,21) reachable from here?).
3. **Prefer plain nav where a route exists.** The boulder solver spent ten
   attempts trying to smash a rock at (21,26) it could neither reach nor face,
   while `find_path` had a 24-move walk ready.
4. **Wild encounters killed legs.** `smash_rock` runs a nested `goto` whose
   default is `on_battle="raise"`, so a battle inside `use_strength` threw
   `TravelInterrupted` out through `bs.walk` and ended the run two cells into
   a leg. Every leg is now battle-guarded.

Milestones: `goalcomp.state` (inside the goal component), `rocksmashed.state`,
`vr-north-1f.state`, `plateau-upper.state`, **`at-league.state`**.

Next: the Elite Four, which has never been attempted.

## session port-63: Victory Road, measured to exhaustion -- the entrance is a SINK

Every component and every link between them, tested strictly (a "join" counts
only a legal step from a state inside the source component to a cell outside
it, so overlap cannot inflate the count):

| link | strict joins |
|---|---|
| 1F entrance (476) -> 1F goal (188) | **0** |
| 1F entrance -> 1F (42,38) pocket (28) | **0** |
| 1F (42,38) pocket -> 1F goal | **0** |
| B1F A (301) -> B1F goal (104) | **0** |
| B1F A -> B1F (42,2) comp (35) | **0** |
| B1F (42,2) comp -> B1F goal (104) | **0** |
| B2F arrival pocket (65) -> B2F big comp (308) | **0** |
| B2F arrival pocket -> B2F (19,12) pocket (6) | **0** |

Reachable closure from Ever Grande: `1F-476 -> B1F-A-301 -> {B2F-65, 1F-28}`.
Terminal. The exit (39,5) lives in the 188-cell component whose only feeder is
B1F(20,21), which sits in the 104-cell component, whose only non-circular
feeder is B2F(19,12) -- a SIX-cell pocket that nothing reachable touches.

**Why each near-miss fails, precisely.** The pocket boundaries are elevation
seams, not walls:

- B2F's pocket touches the 308-cell component at exactly four cells, all
  `z=15 bridge -> z=4 grass, collision 0`. Arriving from B1F(30,25) carries
  **z=3**, and a z=3 walker may not step onto z=4.
- The pocket contains only elevations {3: 59, 15: 6} and its carried level is
  always 3 -- **no elevation-0 wildcard tile exists inside it**, and z=0 is the
  only way the engine lets you change level (`IsZCoordMismatchAt` returns FALSE
  when the player's z is 0). So the level can never change in there.
- 1F's (42,38) pocket is 28 cells of **uniformly elevation 4** with zero
  escapes and zero adjacency to the goal component.

**The movement model is not the culprit, and this was checked rather than
assumed.** `nav.step` implements the engine's rule exactly (`z == 0` or tile
`z in {0, 0xF}` -> no mismatch; else levels must match), and the one crossing
driven live agrees with it: `(26,26) z=3 -D-> (26,27) z=15` returns True in
both. `reachable` visits `(x, y, z)` triples, so bridge cells are not closed
against other levels. And `reachable` treats boulders and Rock Smash rocks as
PASSABLE (they are objects, not terrain), so the model is over-optimistic
about obstacles and still finds no route.

**The one caveat that remains, and it is the next probe.** `grid_drift()`
reported 0 cells on B1F and B2F, but it reads `gBackupMapLayout` -- **only the
loaded window around the camera**. It therefore proves the decode is faithful
where we have STOOD, and says nothing about the far regions holding the 104-
and 308-cell components. Every other explanation is now excluded, so either
those regions decode wrongly, or this save cannot reach the League at all.
Testing it needs a decode check that does not require standing there.

Not a guess, and not a mystery any more: a named, falsifiable claim with the
measurement that would settle it.

## session port-62: the Victory Road blocker, localised to ONE model bug

Asked directly whether the game had been completed and whether a fresh-save
replay had ever been run: **no on both counts.** 8/8 badges and Wallace are
real; the Elite Four has never been attempted. Recording what this stretch
actually established, because it narrows a six-session mystery to one named
defect.

**The dungeon reduces to a single cell.** Measured, not assumed:

    1F(15,40) entrance -> 476 cells, does NOT contain the goal
    1F(42,38)          -> 28-cell dead pocket
    1F(21,32)          -> 188 cells, CONTAINS the exit (39,5)

1F(21,32) is 1F warp 2, which is B1F(20,21). So the entire dungeon is "reach
B1F(20,21)". Warp pairings were calibrated against a known-good pair
(EverGrande warp 2 = (18,41) -> VICTORY_ROAD_1F warp 0 = 1F(15,40), which is
what the game actually does) and are 0-based and correct throughout.

**The map data is not wrong.** `grid_drift()` reports **0 cells** on both
VictoryRoad_B1F and VictoryRoad_B2F -- the decoded grid matches the engine's
live block map exactly. B2F has zero boulders and zero rocks, so its split is
pure terrain. (20,21) is walled by collision-1 at (20,22)-(20,24), and an
elevation-blind, push-enabled search still answers NO SOLUTION instantly, so
it is genuinely a separate component.

**But a real crossing exists that the model refuses.** On 1F the entrance and
goal components are ADJACENT at 72 cell pairs, all through elevation-**15**
tiles -- the engine's wildcard, where `ObjectEventUpdateZCoord` keeps the level
you arrived with. Driven live:

    (26,26) z=3  -D->  (26,27) z=15   step D -> True

`nav.reachable` will not cross that, the engine will. The bridge network
continues to (30-33, 17-19) and its exits into the goal component --
(31,16), (31,19), (32,16), (32,19), (33,16), (33,19) -- are **elevation 3**,
exactly the level carried in from the entrance. That is the missing edge, and
it is a bug in reachability, not in the map.

**And the tool that said otherwise is broken in a nameable way.** A savestate-
backtracking flood reports **36 cells** from B1F(8,3) with Strength on -- the
same "35 cells" two earlier sessions chased -- while `boulder_solver.walk`
demonstrably EXECUTES a 78-move plan from (8,3) to (30,25), hundreds of cells
away. Both cannot be true. The flood is at fault: `step_dir` returns False
instantly when `scene_active()` is set ("scene-owns-input"), and a fresh
`load_raw_state` can leave it set, so most probe steps are refused rather than
walked. Settling 90 frames after the restore does not fix it (tested). Any
future reachability claim from that tool is worthless until this is addressed.

**Next step, named and small:** make `nav.step`/`reachable` carry the player's
CARRIED elevation across 0xF tiles instead of treating the tile's 15 as a
level, then re-derive the components. The candidate link is already measured;
what is missing is a planner that will walk it.

Also this stretch: dex 57 -> 58, and `scripts/league_chain.py` written -- the
B2F route it encodes was disproved (the 39-move plan came from a `solve()`
called WITHOUT `elev`/`start_z`, so it ignored elevation; with elevation it is
no solution). Kept because its docstring records the disproof and the
step-off-the-warp handling is correct and reusable: B1F(30,25) and B2F(30,25)
share coordinates, so arriving lands you on the destination's own warp and the
held step re-fires it -- `take_warp` reports True while the map changed twice.

## session port-61: the frozen screen had a cause, and it was ours

A human watching the widget said "the screenshot hasn't changed for a while."
That report was correct, it was the SECOND time, and chasing it properly found
four real bugs. Worth writing up as a group because they only look unrelated.

**The diagnosis had to be built before it could be made.** ptrace is
restricted here, so `py-spy dump` answers "Permission Denied" and there is no
passwordless sudo -- no external sampler exists on this machine.
`pokeagent/watchdog.py` gained `StallWatch`, which reads `live/<feed>.json`
(the same artefact the human is looking at) rather than the driver, because
the mGBA core is not thread-safe and sampling `driver.pos()` from a second
thread would race the core to diagnose a hang. It separates:

- **PINNED** -- frames advance, position does not. The decision loop is
  pressing something that cannot work. Recoverable: abandon the map.
- **WEDGED** -- frames do not advance either. Blocked in Python; only a stack
  helps.

It dumps every thread on either. It named the bug on its FIRST firing.

**Bug 1 -- goto re-asked to clear the road every round.** The stack was
`pace_map -> goto:675 -> clear_the_way -> smash_rock -> goto:677`. The
`_clearing` re-entrancy guard was fine (nesting is exactly one level); the
fault is that the outer `goto` consulted `clear_the_way` on each of its 144
rounds, and each ask walks four sides of every rock with a fresh 144-round
inner goto. ~10 million frames, about two hours, while every counter inside
`goto` reports progress. Measured: Victory Road B1F (9,9) facing D, frames
138,164,696 -> 138,183,657 in twelve seconds, PNG byte-identical. Now once
per call.

**Bug 2 -- nothing bounded a pacing walk.** `goto` honours `_journey_deadline`
only when one is set, and pacing never set one, so a single walk could eat the
whole sweep. Now armed per walk (60s), and `smash_rock` approaches with
`max_replans=3` -- stepping onto an adjacent cell is not a journey.

**Bug 3 -- `nav.exits` was uncached.** Computing a warp's landing decodes the
DESTINATION map's grid, so `exits` decoded one grid per warp and `route_legs`
asks every map in the graph. This is the call this journal has blamed three
separate times without fixing it. Memoised (pure function of shipped map
data): 23ms -> ~0ms.

**Bug 4 -- the widget was never reloading.** `install.sh` plus the shell's own
"Local plugin changed, reloading: poke.run" does NOT re-instantiate the bar
module, so a previous flicker fix was deployed and never ran -- `cmp` said
deployed == repo and the OLD code was executing. Proven by deploying a Panel
with a new `diag()` IPC function: `omarchy-shell poke.run diag` answered
"Function not found." until `omarchy restart shell`. **Any QML change to this
widget requires `omarchy restart shell`.**

With it actually running, six reflow causes above the frame were fixed
(Repeaters rebuilding every delegate 4x/s because they bind to a JS array
whose content changes each publish; bar-module width oscillation; hero pill;
error line; objective-bar collapse; sprite-decode). Framebuffer top went from
oscillating 607/609/591/675 px -- 76px peak-to-peak, 6 reversals in 24
samples -- to monotone 649/651/675 and then constant.

**One thing I got wrong.** `write` replaced two existing files outright
(`pokeagent/watchdog.py`, `tests/unit/test_watchdog.py`), which broke three
test modules at COLLECTION time -- and a collection error reports no counts at
all, so a suite that could not even be collected got pushed. Recovered from
git; StallWatch now appends to the existing module. The tell was a test count
that went DOWN (652 -> 649) while I was adding tests. **A green suite with a
smaller number in it is not green.**

**Also settled, with evidence.** A scout's plan claimed the harness was
reading version-merged wild tables (Ruby's Seedot where Sapphire has Lotad).
It is not: `dex.WildTable` reads `gWildMonHeaders` from the LIVE ROM for
exactly that reason and its docstring names the trap. Verified -- 112 species,
zero Ruby-only, all four Sapphire markers present. The contamination was in my
own ad-hoc scratch file, not the harness.

**Dex 55 -> 57** (HARIYAMA, LOUDRED) off Victory Road, and the MACH BIKE
collected. Also worth recording: **max achievable on this save is 177/202**,
not 202. The 25-species gap is 6 unchosen-starter, 7 Ruby exclusives, 6
trade-only evolutions, 3 event-only, KYOGRE (one-shot Cave of Origin event
consumed before Wallace), and 2 from the unchosen fossil line -- each cited to
`pret/` source in the plan.

## session port-60: Victory Road's WILDLIFE was never gated -- dex 55 -> 57

The dungeon has blocked this run for six sessions, and I had been treating it
as one problem. It is two, and only one of them was ever hard:

- **The League EXIT** needs the full traversal to 1F(39,5). Still unsolved.
- **The encounter tables do not.** They are per-MAP, so standing anywhere on a
  floor rolls that floor's table. The parts already reachable -- 1F's southern
  region, B1F's entrance pocket, B2F through the proven (30,25) crossing --
  hold ARON, MAKUHITA, HARIYAMA, LAIRON, LOUDRED, WHISMUR, MEDITITE, MEDICHAM,
  MAWILE and SABLEYE between them.

Ten species, the largest single block the dex was missing, and none of them
needed the traversal at all. Two are already banked (HARIYAMA, LOUDRED) and
the sweep is on B1F for MEDICHAM/MEDITITE as I write this.

Two fixes made it reachable:
- `collect.goto_map` now CLIMBS for `VictoryRoad*`/`EverGrandeCity` instead of
  routing. Plain routing has no concept of the waterfall at (18,68), so every
  earlier attempt logged "could not reach VictoryRoad_B1F" and the sweep
  silently skipped all three floors.
- `scripts/vr_hunt.py` restricts a sweep to those floors, because the generic
  collector orders maps by species count and Victory Road never came up.

Also collected the **MACH BIKE** -- a listed missable that had sat in Mauville
for the whole run.

Worth recording as a pattern, because it is the third time: **a heuristic
built for one objective quietly vetoed another.** The ball reserve refused
dex-new catches, the team-merit scorer refuses already-caught species (which
is correct, and is why the Victory Road declines are not a bug), and map
ordering by species count refuses to ever try the hard-to-reach map. Each time
the fix was to make the DEX the first question asked, not the last.

Money also moved 322 -> 4,498, because Victory Road's own trainers pay.

## session port-59: the dex was starved by a WRONG PREMISE -- 38 -> 47

**Wild battles pay no prize money in Pokemon. Only trainers do.** Every
earlier "grind for money" in this journal was grinding wilds, which is why the
run sat at 38/114 with 3 balls and 130 money across three sessions and no
amount of pacing ever recovered.

Meanwhile the save had **547 of 693 trainer flags unset**
(`TRAINER_FLAG_START 0x500`, `NUMBER_OF_TRAINERS 693`) -- a few hundred
thousand in prize money untouched, and free wins with an L100 in front.

`scripts/trainer_farm.py` fights them, taking trainers from each map's own
`object_events` (`trainer_type != NONE`) so no walking is wasted. Measured:
**money 130 -> 5,798 in fifteen minutes**, then -> 10,926 in thirty.

Then `Collector.earn_money` wires that into the collector itself, so it earns
when it cannot afford a ball instead of "hunting anyway" with an empty bag.
The chain closes on itself now: trainers -> money -> balls -> catches, no
human alternating two scripts.

**Measured end to end today:**

| | dex caught | note |
|---|---|---|
| session start | 38 | 3 balls, 130 money, stuck |
| ball-reserve fix (port-58) | 40 | reserve was refusing dex-new catches |
| first funded collector run | 42 | 18 balls bought from farmed money |
| self-funding collector, 15 min | **47** | five new species in one window |

Milestones: `saves/dex40.state`, `saves/funded.state` (10,926), `saves/dex47.state`.

Rate is roughly five catches per fifteen minutes while routes still have
unbeaten trainers, and 529 remain. This is the first time in the run that the
Pokedex has a mechanism that does not need a human in the loop.

## session port-58: the ball reserve was refusing every catch -- dex 38 -> 40

`BALL_RESERVE = 3` was checked BEFORE the dex override, so holding exactly
three balls made `balls <= RESERVE` true and declined **every** catch, dex-new
included. The run had been sitting at 3 NET BALLs and 38/114, routing
correctly to maps with five new species each and refusing all of them, while
the reserve it was protecting had nothing left to protect for.

The comment two lines below it already argued the opposite -- "THE DEX COMES
FIRST... a species the dex has never recorded as CAUGHT is worth a ball on
sight" -- so the ordering simply contradicted the stated priority. A reserve
is for choosing BETWEEN catches, never for refusing the only kind that closes
the objective.

Fixed and measured within minutes: **dex 38 -> 40**, banked as
`saves/dex40.state`. The catcher now engages on sight, verified in the log
against NUMEL, CARVANHA, ELECTRIKE, MANECTRIC, CORPHISH, ROSELIA, VOLBEAT and
SURSKIT -- eight distinct new species in one sweep where the previous run
produced literally none. Four unit tests pin both halves (dex-new needs one
ball; the reserve still applies to species already registered).

**The constraint is back to supply.** Balls are now empty and money is 130.
With Victory Road unsolved there are no trainers left to farm, and wild
battles pay little, so the dex advances roughly two species per shopping trip.
The two levers, in order: solve Victory Road (the Elite Four is worth far more
than every wild battle in Hoenn combined, and an L100 PELIPPER wins it on
arrival), or find a repeatable money source that does not need the League.

Worth noting for whoever picks this up: the L100 also inflated the team-value
scorer's level floor, so non-dex-new catches now score around -17 with
"owes 73 level(s) of training to reach the party's L100 floor". That is
harmless while the dex override runs first -- which it now does -- but it
means the scorer's level term is effectively dead weight for the rest of this
run.

## session port-57: the pocket, named -- and the BFS is the thing that is wrong

The reachable set from `B1F(8,3)` is **35 cells**, identical across five
configurations: raw-core fork, driver save/load fork, unarmed, armed with
Strength plus an aimed Rock Smash, and a long (4x90 frame) settle that
resolves battles before believing a position. The cells are:

```
(3,7) (4,4..10) (5,4) (5,7) (5,10) (6,4) (6,7) (6,10) (7,3) (7,4) (7,7)
(7,10) (8,4) (8,7) (8,10) (9,3..12) (10,4) (10,5) (10,6) (10,10)
```

Everything is x<=10. Rendered against the map, the pocket is **sealed at
x=11** on rows 4, 5, 10 and 12, and the way out is **row 13**, where x9
through x15 are all ordinary walkable grass at elevation 3.

The pocket contains **(9,12)** and not **(9,13)** -- the cell directly below
it, elevation 3, behaviour 0x8, collision 0, plain grass, indistinguishable
from its neighbour. There is no rule under which the engine refuses that step.

**So the BFS is the instrument that is wrong**, and it disagrees with a route
the boulder solver has walked repeatedly: (4,6) -> (17,12), smash (18,12) ->
(34,12) -> (30,25), logged at 110-122s on several separate runs. A search that
cannot reproduce a move we have watched succeed is measuring itself.

Ruled out along the way: the fork mechanism (both paths give 35), an unarmed
player (Strength + aimed A gives 35), and mid-battle position reads (a 4x90
frame settle gives 35).

**Leading suspect, and the next thing to try:** `step_dir(verify=True)` reads
`pos()` immediately before the press, and immediately after a state load that
read may still be stale -- making a real move look like a no-op, which this
search prunes. A `settle(8)` after `_restore` was added and **measured: still 35**, so that
suspect is eliminated too. Six independent configurations now agree on the
same 35 cells.

**Where that leaves it.** The disagreement is not in the fork, the arming, the
battle handling, or the restore timing. The one remaining difference between
the two instruments is the CALL PATH into a step: the boulder walk drives
`step_dir` inside its own plan loop, having just computed a route on a grid it
trusts, while this search calls `step_dir` cold on a freshly restored state
for each of four directions. Instrument `step_dir` directly -- log its
before/after `pos()` reads and its `last_step_reason` for the specific move
(9,12) -> (9,13) -- and the answer is one line of output away. That single
cell is a complete, self-contained reproduction: plain grass, elevation 3,
collision 0, one step south of a cell the search accepts.

**RETRACTED, next morning.** That reading was wrong and the tool was right.
Driving the single move by hand settles it: standing on (9,12), `step_dir("D")`
returns False three times with `blocked moving D from (9, 12)`, no battle, no
scene -- and `step_dir` PRESSES before it reports (it even re-presses for the
turn-first case), so the engine was asked and refused. The 35-cell pocket is
real.

Every input to that decision has now been measured, and they all say the move
should be legal:

| input | value |
|---|---|
| player's carried elevation at (9,12) | **3** |
| (9,13) elevation, static and live | **3** |
| (9,13) collision, static and live | **0** |
| (9,13) behaviour | 0x08 `MB_UNUSED_CAVE`, same as (9,12) |
| `grid_drift()` on B1F | **0 cells** |
| objects at or beside (9,13) | none -- nearest is the boulder at (9,10) |

So this is a fully-characterised anomaly rather than a mystery: identical
level, no collision, no object, no drift, and the hardware still says no. Directional collision is eliminated as well: the raw nibbles along rows 11-13
are only ever 0 or 1 (never the 2/3 that would encode a one-way tile), and
both (9,12) and (9,13) read **0**.

```
y=11  x8:1 x9:0 x10:1 x11:1 x12:1 x13:1 x14:1 x15:0 x16:0
y=12  x8:1 x9:0 x10:1 x11:1 x12:1 x13:1 x14:1 x15:0 x16:0
y=13  x8:1 x9:0 x10:0 x11:0 x12:0 x13:0 x14:0 x15:0 x16:1
```

So the corridor really is open in the data and the engine really refuses to
enter it from the north. Also eliminated, by direct measurement while standing on (9,12): the player is
**on foot** (`gPlayerAvatar` flags `0x1`, `is_surfing()` False), carrying
elevation **3**, and **already facing D** -- so this is not a
turn-then-step artefact, not a surf state, and not a mismatched level. B1F has
**no `coord_events` and no `bg_events`** at all, so no script can be vetoing
it either.

The full elimination list for `(9,12) -> (9,13)`: elevation (both 3),
collision (both 0, nibbles only ever 0/1 so no one-way encoding), objects
(none nearer than the boulder at (9,10)), grid drift (0 on B1F), coord/bg
events (none on the map), battle and scene interference (checked live), avatar
state (on foot), and facing (already D). Every input says legal; the hardware
says no.

**The veto chain, read from source.** `GetCollisionAtCoords`
(`pret/src/event_object_movement.c:4463`) is the only gate, and it has exactly
five clauses:

```c
if (IsCoordOutsideObjectEventMovementRange(objectEvent, x, y))        return 1;
else if (MapGridGetCollisionAt(x, y) || GetMapBorderIdAt(x, y) == -1
      || IsMetatileDirectionallyImpassable(objectEvent, x, y, direction)) return 2;
else if (objectEvent->trackedByCamera && !CanCameraMoveInDirection(direction)) return 2;
else if (IsZCoordMismatchAt(objectEvent->currentElevation, x, y))     return 3;
else if (DoesObjectCollideWithObjectAt(objectEvent, x, y))            return 4;
```

Measured against `(9,12) -> (9,13)`, three clauses are eliminated outright:
* `MapGridGetCollisionAt` -- 0, static AND live.
* `IsMetatileDirectionallyImpassable` -- it tests the CURRENT tile's behaviour
  with the opposite-direction predicate and the DESTINATION's with the forward
  one (`:4519-4520`), and both cells read `0x08` identically in the static grid
  and in `live_grid`. No `MB_IMPASSABLE_*` (0x30-0x37, 0xC0-0xC1) anywhere near.
* `GetMapBorderIdAt` -- the cell is interior.

**Two of those three are now eliminated as well:**
* **Camera** -- `CanCameraMoveInDirection` (`fieldmap.c:570-580`) only ever
  returns FALSE when `GetMapBorderIdAt` says the neighbouring camera position
  is `CONNECTION_INVALID`. (9,13) is interior, so it cannot fire.
* **Object collision** -- `live_npcs()` returns an EMPTY list while standing
  on (9,12), so `DoesObjectCollideWithObjectAt` has nothing to collide with.
* **`IsZCoordMismatchAt`** -- measured directly: the player is
  `objectEventId = 0` on this map and both slot-0 and player-slot reads give
  `currentElevation = 3`, matching the destination.

**ALL FIVE CLAUSES ARE NOW MEASURED AND EXCLUDED.** The last one,
`IsCoordOutsideObjectEventMovementRange`, reads `range.as_nybbles` at struct
offset `0x19`, and on the player it is **`0x00`** -- both nybbles zero, so the
function returns FALSE unconditionally (`:4500`, `:4507` are both skipped).

That is a hard result rather than a dead end: **the refusal cannot originate in
`GetCollisionAtCoords`.** Something earlier in the movement pipeline is
rejecting the input -- the tile-transition state machine
(`PlayerCheckIfAnimFinishedOrInactive`, `gPlayerAvatar.tileTransitionState`),
`sub_8058EF0`/ledge handling, or the harness's own press timing -- and that is
where the next session should look, not at terrain.

**One concrete discovery to carry over:** the object event stores coordinates
in OFFSET space. On this save `initialCoords` and `currentCoords` both read
**(15,10)** while `Driver.pos()` reports **(8,3)** -- a difference of exactly
`MAP_OFFSET` (7) on both axes. So `GetCollisionAtCoords` is called with
offset coordinates, and any future WRAM comparison against a nav cell has to
add 7 to both axes or it is comparing different tiles. Our own reads happened
to be self-consistent (both map-local), which is why the behaviour and
collision comparisons above are still valid.

Superseded -- the clause that WAS unmeasured:
`IsCoordOutsideObjectEventMovementRange(objectEvent, x, y)`
(`event_object_movement.c:4467`), which reads the object's own `range` /
initial-coords fields. It is the first clause in the chain, it returns 1, and
it does not look at terrain at all. Read
`gObjectEvents[gPlayerAvatar.objectEventId]`'s `rangeX`/`rangeY` and
`initialCoords` and compare against (9,13). If a range is set on the player's
object event, that is the whole answer and it explains every measurement above.

Superseded suspect list, kept for the record:
1. **`trackedByCamera && !CanCameraMoveInDirection`** -- the player IS
   camera-tracked, and this clause returns 2 with no reference to the tile at
   all. Nothing in this repo models it.
2. **`IsZCoordMismatchAt(objectEvent->currentElevation, ...)`** -- note it reads
   `currentElevation` off the OBJECT, not the map. `Driver.elevation()` may be
   reporting a different field, in which case "carrying 3" was never measured.
   Read `gObjectEvents[gPlayerAvatar.objectEventId].currentElevation` directly.
3. **`IsCoordOutsideObjectEventMovementRange`** -- a movement range on the
   player's own object event would refuse steps regardless of terrain.

Also worth noting for the acro-bike theory: `check_acro_bike_metatile` can
raise a collision from 0 (`field_player_avatar.c:611`), and this run has never
had the ACRO BIKE -- but the rail behaviours are 0xD3-0xD6 and these tiles are
0x08, so it is not that.

The old next step, kept for the record: `TryStepOntoMetatile` /
`GetCollisionAtCoords` in `src/field_player_avatar.c`, following what else can
veto a step -- the surf/bike state flags, `gPlayerAvatar.transitionFlags`, and
the map's own `coord_event` scripts, none of which are in any model here. One
of those vetoes this move, and finding it in the source is minutes of reading
against the hours of probing that got this far.

What this session did establish beyond doubt: the pocket is real, its
boundary is exact, and the boulder solver's claim to have walked from it to
(17,12) needs re-auditing -- that log is now the only evidence that disagrees
with six instruments.

## session port-56: SEA BIRD is **L100**, and the Fly cursor is not the landing

Two facts worth having, one of them very good.

**The party has an L100.** `SEA BIRD` (PELIPPER) is level 100 with experience
exactly 1,000,000 -- the Medium-Fast cap -- and 296 max HP, so it is genuine,
not a misread. It carries SURF / SHOCK WAVE / FLY / WING ATTACK. The
retry-bar training loop from port-41 did this unattended over roughly a day,
once port-39's decline-loop fix let experience flow at all.

**That means the Elite Four is winnable the moment it can be REACHED.** No
tactics work is needed; the only blocker is Victory Road.

**The Fly bypass does not exist, and here is the trap.** Entering the city
once sets `FLAG_VISITED_EVER_GRANDE_CITY` (verified: False on
`badge8-won-real.state`, True after `league_run` climbs the falls), and the
region-map cursor for Ever Grande is **(28,10)** -- above the cliff, in the
same region as the League door. That looked like a way to skip the dungeon
entirely. It is not: the cursor is only where the marker is DRAWN, and the
landing is the map's own fly warp at **(18,42)**, on the lower plateau.
Verified live: `fly -> True EverGrandeCity (18,42)`, with (18,5), (18,6) and
(18,27) all outside the resulting 471-cell component.

Kept anyway: `league_run.to_city` now flies when the flag is set, which
retires the four sea legs.

**Also measured:** the emulator-driven BFS reports 35 reachable cells from
`B1F(8,3)` under four different configurations -- raw core fork, driver
save/load fork, unarmed, and armed with Strength plus an aimed Rock Smash --
while the boulder solver demonstrably walks from that same tile to (30,25).
Both cannot be right, and the BFS is the one contradicting a route that has
been verified repeatedly at 110-122s. Its 35-cell pocket is (8,3), (4,6),
(9,10) and neighbours; the proven route leaves it by pushing (9,10) to
(10,10), so the first divergence is one step deep and observable.

## session port-55: the emulator BFS contradicts a proven walk -- restore is lossy

`scripts/explore_bfs.py` searches over real game states: press a direction,
read where the avatar actually ended up, fork with
`save_raw_state`/`load_raw_state`. No walkability model is consulted, so if
the engine moves the player the edge exists.

Unarmed it reached 23 cells; armed with Strength and a face-then-A press
(Rock Smash is how the only proven B1F route opens) it reached **35**, then
emptied its frontier both times.

**That cannot be right, and the contradiction is the useful part.** The
boulder solver has repeatedly walked an 83-move plan from this exact tile to
(30,25) -- hundreds of cells away, verified as recently as port-52 at 110s --
so a search that finds 35 cells and stops is not measuring the dungeon. It is
measuring our restore.

So `load_raw_state` is lossy with respect to something `Driver` depends on:
the avatar's own state block, the scene/callback the driver checks before it
will step, or a cached position the driver does not re-read after a raw
restore. `emu.load_state()` goes through provenance checks and bookkeeping
that the raw path skips entirely, which is exactly where the difference will
be.

**Measured:** switching the fork to `emu.save_state`/`load_state` gives the
SAME 35 cells, so the restore is not the difference. The gap is therefore in
the action sequence, and the prime suspect is now the face-then-A press this
search does before every step: at a boulder that A opens the Strength prompt
again, and the `close_menus()` that follows can eat the step. The boulder walk
never presses A except deliberately, from an adjacent cell, at a rock it means
to smash.

**Next:** make the A press conditional -- only when the faced tile holds a
breakable rock -- and re-measure. If 35 becomes 300+, the harmful press was
it. The 35-cell pocket to compare against is (8,3), (4,6), (9,10) and their
neighbours; the boulder walk's proven route leaves that pocket by pushing
(9,10) to (10,10), so the very first divergence is observable in one step.

The tool itself is worth keeping either way: it is the only instrument here
that cannot be fooled by a wrong model, which is precisely what four sessions
of Victory Road needed.

## session port-54: the decode is FAITHFUL -- so stop trusting interpretation

`grid_drift()` on Victory Road 1F reports **0 cells**, and `live_grid()` with
no rect covers the whole map from `gBackupMapLayout`. So the static `.blk`
decode matches the map the engine is actually walking, exactly. The data is
not wrong, which retires port-53's conclusion.

Everything else was checked and excluded too:
* **Sea route to the upper city:** the 320-cell water component touches region
  A at seven cells (17-22, 56-57) and region **B at zero**. (18,6) -- the
  League's approach tile -- is in B and in neither A nor the water.
* **The eastern pocket:** `1F(42,38)`, reachable from the entrance via
  `B1F(42,25)`, is 28 cells, bbox x30-43 y35-39, **every cell elevation 4**,
  zero cells adjacent to the northern component, and no wildcard (0/15) tile
  to free the level. A genuine dead end.
* **Ledges** are already modelled by nav (two-tile jumps, `nav.step:549`), and
  nav agrees with the solver on every component.
* **Boulders cannot be hiding the seam**: they are objects, not collision, so
  nav's grid sees straight through them -- if a corridor existed behind one,
  nav would have reported the components as joined.

**So the model is faithful and the League is still unreachable, which means
the fault is in INTERPRETATION, not in the data or the search.** The next tool
should therefore not interpret at all: drive the emulator itself. From a
savestate, try each direction, keep what actually moved, restore and repeat --
a BFS over real game states rather than over a decoded grid. The Crystal
harness kept exactly this escape hatch for "the grid is lying"; here the grid
is honest and it is our reading of it that must be wrong, which is precisely
the case emulator-driven search settles without argument.

It is also cheap to scope: run it from `B1F(8,3)` and ask only "can any
sequence reach (17,16)". A yes names the move the model refuses; a no proves
the dungeon needs something we do not yet have at all (an item, a script, a
scene) and turns the question into a decomp read rather than a search.

## session port-53: Victory Road is unreachable IN THE DATA, not in the planner

With the model now faithful -- collision, elevation carried as `z` through the
search, water with Surf mounting, breakable rocks, boulder belief, and
transient bodies excluded -- nav and the solver AGREE on every answer. That
makes the following a statement about the decoded map rather than about
pathfinding, and it is worth stating precisely because it ends a long hunt in
the wrong place.

**The League sits behind a closed loop:**
* Ever Grande City is two regions: lower (152 cells, holds the Victory Road
  door at (18,41)) and upper (231 cells, holds the League door (18,5) and the
  second Victory Road door (18,27)). The band y32-40 between them is solid
  cliff -- checked cell by cell -- and there is no second waterfall (all 96
  MB_WATERFALL cells belong to the one climb at x15-24, y60-67).
* `1F(39,5)` <-> `EverGrande(18,27)`, so the upper city is entered ONLY by
  exiting Victory Road at (39,5).
* (39,5) lives in 1F's northern component (188 cells, with (21,32)), fed only
  by `B1F(20,21)`.
* `B1F(20,21)` lives in B1F comp1 (104 cells, with (17,16)), and comp1 has
  **zero cells adjacent** to the 301-cell entrance component -- walls, not a
  ledge, so nav's ledge model cannot be the gap either.
* comp1's only other door, (17,16), leads to a 6-cell B2F pocket whose only
  door leads straight back to (17,16).

Every door out of the entrance system was tested and lands in a pocket:
(30,25) -> B2F's large region (one door, back to B1F); (42,25) -> `1F(42,38)`,
a 28-cell pocket at elevation 4; (5,26), (42,2), (17,16), (20,21) all
unreachable.

**So the decoded grid is missing a connection the ROM has.** The next move is
not more search: dump the LIVE block map for each Victory Road floor and diff
it against the static `.blk` decode (`grid_drift()` / `sync_grid()` exist for
exactly this). A `changeblock` or a mis-decoded collision byte on one floor
would explain all of it at once, and every measurement above becomes its test.

**What was gained anyway:** the planner is now correct on elevation, which
was a real and general bug -- it silently mis-planned every multi-level map in
the game, not just this one -- plus water mounting, two-fact learning
(walls vs no-push), corroboration before believing a wall, live push
detection, and a belief that no longer forgets. B1F still crosses in 110s.

## session port-52: the solver now carries LEVEL, and B1F still crosses

`solve()` searches over `(x, y, z)`, with `_step_z` mirroring `nav.step` and
`ObjectEventUpdateZCoord`: a concrete destination level that differs from the
one you carry is illegal, 15 keeps your level (a bridge), 0 frees it. This is
the fix port-51 specified, and it gets BOTH cases right where a static filter
could only get one:

* Victory Road 1F's y=24 row is correctly unreachable from the e4 side.
* B1F's route to (30,25) -- which CHANGES level through a wildcard tile --
  still works: `B1F->(30,25): True VictoryRoad_B2F (30,25)` in 110s.

Also sealed the grid edge. `elev.get(cell, 0)` handed out-of-bounds cells the
wildcard level, so the search stepped off the map and back on, walking round
any seam it liked. The new tests caught it; it would have bitten real maps too.

**Connectivity re-measured with the faithful model** (this supersedes port-50,
because those numbers came from a level-blind search):
* `B1F(8,3)` -> (30,25) YES. -> (17,16), (20,21), (42,2), (5,26) NO;
  -> (42,25) reaches (20,5) and stops.
* `B2F(30,25)` -> (19,12), (43,2), (5,26) all NO, and the region is large
  (a failed walk wandered to (20,68)).
* `1F(15,40)` -> (39,5), (21,32) NO -- correctly now, by the elevation rule.

So the reachable set from the entrance is {1F south, B1F entrance region,
B2F(30,25) region} and none of them touches the goal. Every cheap explanation
is now excluded: collision, elevation, water, breakable rocks, boulder
positions, off-camera objects, and transient bodies are all modelled.

**The one model gap left is LEDGES.** `nav._dir_blocked` gives them to
`nav.step`, but `solve` has no concept of them -- it treats every cell as
bidirectional. That makes the solver too PERMISSIVE, not too strict, so it
cannot be why a route is missing... unless a ledge is the intended way IN to
one of these regions, in which case the real route descends a ledge the
planner will not plan because it cannot see the one-way edge as an edge at
all. Worth checking directly: dump `nav`-reachability from `B1F(20,21)` and
`1F(21,32)` BACKWARD and see which region actually feeds them.

## session port-51: the Victory Road barrier explained -- it is ELEVATION

The y=24 wall that three sessions blamed on a bad decoded grid is the engine
enforcing an elevation seam, and the numbers say so exactly:

```
(7,24) e3   (8,24) e3   (9,24) e3     <- refused, every time
(6,25) e4   (7,25) e15  (8,25) e15    <- the bridge row
(9,25) e15  (10,25) e4
```

Elevation 15 is a bridge: `ObjectEventUpdateZCoord` keeps whatever level you
arrive with. Coming from the e4 cells you cross y=25 still carrying 4, and
4 -> 3 is illegal -- so the step north is refused by rule, not by terrain.
`nav.step` already models this correctly (line 608). **The boulder solver did
not**: `snapshot()` built its wall set from `collision` alone and never looked
at elevation, so it planned routes the engine cannot walk and then learned the
refusals as mysterious walls.

Adding the filter proved the diagnosis on 1F and **broke B1F**: the verified
route to (30,25) changes level through a wildcard (elevation-0) tile, which a
static filter keyed on the CURRENT level forbids. `walk -> True
VictoryRoad_B2F (30,25)` became an instant "no solution".

So it ships **off by default** with five tests pinning both behaviours, and
the real fix is named: `solve()` must carry `z` in its search state the way
`nav.step` does -- search over `(x, y, z)`, using `_next_z` for transitions.
That is a contained change to one function and it should open the dungeon,
because every dead end measured in port-50 was a seam this model cannot
express.

Re-verified after the revert: `walk(30,25) -> True VictoryRoad_B2F (30,25)`
in 122s, smashing (18,12) and re-planning around a scene interrupt.

## session port-50: the Victory Road connectivity map, measured

Stop guessing at this dungeon; here is what was actually probed, each with a
clean cache and the corrected models.

**Reachable, verified:** `1F(15,40) -> (9,14) -> B1F(8,3) -> (30,25) -> B2F`.
The B1F leg smashes (18,12), survives a `scene-owns-input` interruption at
(34,12), re-plans and lands on `VictoryRoad_B2F (30,25)` in 122s.

**Measured dead ends** (boulder solver, smashing and Surf enabled, several
tries each):
* From `B2F(30,25)`: (43,2) NO, (19,12) NO, (5,26) NO. It walks as far as
  (20,68), so the region is much larger than the 65 cells the static grid
  claims -- and still none of the other three doors.
* From `B1F(8,3)`: (17,16) NO, (20,21) NO, (42,2) NO, (5,26) NO. Only
  (30,25) solves.
* From `1F(15,40)`: (21,32) NO, (42,38) NO, (39,5) NO. The barrier is the row
  y=24: (7,24), (8,24), (9,24) each refuse a northward step **repeatedly**, so
  they are real terrain rather than noise (the corroboration counter reached
  2/3 on (7,24) in a single run).

**What that means.** The goal (39,5) shares 1F's northern component with
(21,32); (21,32) is fed only by `B1F(20,21)`; that sits in a B1F region whose
only other door is (17,16); and (17,16) pairs solely with `B2F(19,12)`, which
cannot be reached from the B2F region we can get to. Every measured edge is a
dead end, yet the dungeon is completable in the real game -- so a real edge is
missing from the MODEL, not from the map.

**Prime suspect: elevation and ledges.** The y=24 barrier behaves exactly like
a one-way ledge row, and `nav._next_z` is the same code that made
`EverGrandeCity`'s waterfall look walkable from sea level. A session on that
model -- ledges as directed edges, elevation seams honoured -- is far more
likely to open this than more search. Every fact above is a test case for it.

**Kept regardless:** a wall now needs `WALL_CONFIRMATIONS` (3) independent
refusals before it is believed. Five B1F "walls" appeared in one sweep and
killed a route that had crossed cleanly minutes earlier; three separate
transient causes have masqueraded as terrain here (a body on the tile, an
unmounted water tile, a scene owning input). Corroboration is cheap; a false
permanent fact costs a verified crossing.

## session port-49: bodies are not terrain -- B1F crossing re-verified clean

`walk(30,25) -> True VictoryRoad_B2F (30,25)` in 122s, from
`badge8-won-real.state`, with every corrected model in play: it smashed
(18,12), survived a `scene-owns-input` interruption at (34,12), re-planned,
and warped down. That is the crossing working end to end rather than by luck.

The last false-wall source was **wandering trainers**. Victory Road B1F has
three, and one standing on (8,7) refuses a step exactly as a wall does -- then
walks away. Written to disk as permanent it made the whole floor unsolvable
and contradicted a crossing this repo had already proven. Refusals into an
occupied cell are no longer learned.

That is the third false source the cache has had -- water, refused pushes, and
now bodies. **The pattern is the lesson: persisting a negative fact requires a
reason to believe it is permanent.** Each of the three looked identical at the
point of failure (a step was refused) and only one of them was terrain.

Running totals for the dungeon: eleven distinct faults fixed, all verified,
and the full `victory_road.py` sweep now explores B1F to (25,9), 1F to
(16,34), reaches B2F, and attempts the goal door at (39,5) -- where before it
ping-ponged between two rooms.

## session port-48: three more model bugs; the belief finally holds all 8 boulders

Each of these was found by making the executor say what it was doing, and each
was silently corrupting the model the planner ran on.

1. **"Am I pushing?" was answered from a plan-time snapshot.** `pushing =
   ahead in known` used a belief that could be stale by execution time, so the
   engine shoved boulders the executor did not know about -- the push guard
   was never consulted and no `refusing push` line ever appeared. The cell
   ahead is always adjacent, so `live_npcs()` is authoritative there; it is
   read live now.
2. **A refused push was recorded as a wall.** B1F takes a push down from (4,7)
   and the engine refuses the landing at (4,8). Stored as a WALL that made the
   alcove at (4,6) unwalkable too and killed an otherwise-fine 83-move plan,
   leaving an instant "no solution" where planning had just worked. Refused
   pushes now go to a separate no-push set, persisted alongside the walls and
   fed to the planner as illegal boulder destinations.
3. **The belief forgot boulders it merely could not see.** `belief()` dropped
   any believed boulder inside `_near`'s radius that was absent from
   `live_npcs()` -- but that radius is 16, far wider than the engine's object
   window -- so walking away emptied the model: seven boulders down to three
   by (15,13), and the next plan computed against a room that does not exist.
   Boulders only move when we shove them, and `note_push` already records
   that, so absence now neither adds nor removes.

**Measured effect:** the belief at (9,7) now lists all eight boulders
[(4,7), (9,10), (20,5), (20,26), (21,4), (21,25), (34,4), (35,6)] where the
same point previously reported three.

**Still open:** the 83-move plan runs to completion and ends at (9,7) rather
than (30,25), with no refusal logged. Since every refusal path now logs, the
plan is finishing while diverging -- moves are being consumed that the engine
does not perform the way the model expects. Next: log position after every
move against the expected cell and find the first divergence; that single
trace should name the remaining fault.

## session port-47: a learned wall is permanent -- so it had better be true

The learned-wall cache from port-46 **poisoned itself and broke a crossing
that had already worked.** Sequence, all verified:

* port-45 proved B1F crossable: `walk -> True VictoryRoad_B2F (30,25)`.
* port-46 added persistence -- before the Surf model existed. Every water
  refusal was recorded as solid rock, so `VictoryRoad_B1F` acquired false
  walls at (25,10), (26,10), (32,10), plus (9,3) directly beside the
  entrance.
* With (9,3) walled the run was forced into the dead-end alcove at (4,6)
  every attempt, and the solver answered `no solution to (30,25)` **in 0s**.

Two fixes: water is never learned as a wall (a water refusal means we failed
to mount Surf, not that the tile is solid), and the poisoned file is deleted
so the map re-learns under the corrected model. Re-verified immediately after:
`plan 0: 83 moves (4,6) -> (30,25)` -- planning restored.

**The lesson is general and worth keeping:** a cache of negative facts is only
as good as the model that produced them. Persisting "this is impossible" from
a harness that was missing a capability bakes the missing capability into the
map forever. Any future negative cache needs a model-version stamp.

**Guard landed but does not fire on the push that matters.** The route-aware
check is in and unit-tested (a push the door-only check waves through is now
refused), yet the live run still shoves (4,7) west to (3,7) with no
`refusing push` line in the log. The guard is keyed on
`pushing = ahead in known`, so the engine is moving a boulder the BELIEF does
not have at that cell at that moment -- the executor never recognises the move
as a push, never calls the guard, and the engine shoves anyway. Next session:
log `known` at the moment of each step and find where the belief and the
engine part company; the fix is likely to re-read `belief(d)` per move rather
than once per plan.

**Still open:** the first push of that 83-move plan shoves (4,7) west to (3,7),
after which `(30,25)` is unreachable and the walk re-plans into failure -- the
floor stays recoverable (the stranding guard does its job; a door is still
reachable) but the ROUTE does not survive. The guard answers "can I still
leave?", which is the wrong question for this case; it needs to answer "is my
target still reachable?". That is the next change: pass the walk's target into
`_push_keeps_a_door` and test reachability to IT, not merely to any door.

## session port-46: the grid is wrong in a *class* of cells -- so learn it

Two model fixes on top of port-45's B1F crossing.

**Water was never in the model.** `snapshot()` marks a wall only where
`collision` is set, and water has collision 0 -- so the planner happily routed
across water the player cannot step onto, every such step was refused, and the
refusal was then banked as a permanent wall. That is why Victory Road's
water-linked halves read as disconnected and the crossing ping-ponged between
B1F(30,25) and B2F. The executor now faces the water and mounts Surf before
the step; stepping back onto land needs no help, the engine dismounts itself.

**Refused cells are remembered across runs** (`data/learned_walls.json`).
Victory Road refuses a whole class of ordinary steps the decoded grid calls
open -- (7,25), (8,25), (9,25), (9,26), (16,34), (16,35) on 1F alone, none of
them holding an object, all of them almost certainly elevation seams the
`_next_z` model gets wrong. Held only in memory, each crossing burned its
budget rediscovering the same walls. On disk the map gets strictly more
accurate every run. Four unit tests cover it.

Also fixed here: `reachable_warps` pre-filtered candidate doors with
`nav.reachable`, which walks the STATIC grid and cannot see past a rock we
intend to smash -- B1F offered only two of its seven doors and (20,21), the
one that leads to the goal component, was never even considered.

**Honest status:** the crossing still does not reach (39,5) inside one budget.
It enters, descends, crosses B1F, reaches B2F, and now considers every door,
but it strands itself with its own pushes -- Sokoban is one-way -- and a
stranded floor can only be undone through a door it can no longer reach.

The next lever is not more search. Either make `solve` refuse a push that
leaves no door reachable (a connectivity check on the post-push state), or
carry an ESCAPE ROPE so a stranded floor is always recoverable. The party has
neither an Escape Rope nor Dig right now, which is why a bad push is currently
terminal.

## session port-45: Victory Road B1F **crossed**; the rest is a Surf model

**B1F is solved.** `walk -> True VictoryRoad_B2F (30,25)`, verified live from
`badge8-won-real.state`. Four stacked faults in `boulder_solver`, each hiding
the next:

1. **The rock model was inverted.** `block = others | rocks if smashing else
   others` treated a breakable rock as PERMANENT when Rock Smash was
   available, and walkable when it was not. B1F is gated by the rock at
   (18,12), so the solver answered "no solution" to a route the executor opens
   with one A press.
2. **Rocks were invisible off-camera** -- they came from `live_npcs()` alone,
   so (18,12) was not in the model at all and every plan walked into it:
   `step R refused at (17,12)`. `rock_belief()` seeds from the map's own
   object table, exactly as boulders already did.
3. **Absence was read as "smashed".** The first `rock_belief` inferred removal
   from a rock missing within `_near`'s radius 16 -- wider than the engine's
   object window -- and deleted the very rock it had just seeded.
4. **`_smash` recursed.** Once rocks were walkable to the planner, its
   approach walk planned through another rock and called `_smash` again until
   the stack blew. Getting NEXT TO a rock never needs one removed.

Supporting fixes that turned single-shot attempts into a convergent search:
the executor logs WHY a step was refused and **learns** cells the engine
refuses but the grid calls open (B1F refuses (5,10) and (9,11), neither
holding an object); `reset_floor` uses each floor's own back door and is
boulder-aware; the warp set is retried rather than exhausted once; a
`TravelInterrupted` from a wild encounter no longer kills the process; and
`league_run` heals at Ever Grande's Center (27,48) first, after a crossing
whited out to Sootopolis with the lead at 0 HP.

**What still blocks the exit, with the evidence:** the crossing settles into a
B1F(30,25) <-> B2F(30,25) cycle. The goal (39,5) sits in a 1F component
reachable only through B1F (20,21), and B1F comp1 -- holding (20,21) and
(17,16) -- pairs only with B2F comp2 (6 cells) in the land-only graph. The
missing edges are almost certainly WATER: `snapshot()` calls a cell a wall
only when `collision` is set, and water has collision 0, so the solver plans
straight across water the player cannot walk on. Those refusals are exactly
the (5,10)/(9,11) cells it keeps learning as walls.

**Next step, concretely:** teach `snapshot`/`solve` that water is passable
only while surfing, and let the executor mount and dismount (the driver
already handles the Surf prompt -- `EverGrandeCity` proved it by routing a
dismount on its own). That converts the learned-wall trickle into a correct
model and should open the (20,21) route.

## session port-44: Victory Road -- enters, descends, plans; execution diverges

`scripts/victory_road.py` is a warp-graph walk with a boulder-aware `reach`.
What works, verified repeatedly from `badge8-won-real.state`:

* Enter at (18,41), land `VictoryRoad_1F (15,40)`, walk to the stairs (9,14)
  and descend to `VictoryRoad_B1F (8,3)`.
* `use_strength()` arms the floor (FLAG_SYS_USE_STRENGTH is per-map and was
  simply OFF -- that alone is why plain routing reported "walked 144 chunks
  without arriving" while the decoded grid insisted the target shared a
  301-cell component).
* The solver finds a real route: **"plan 1: 68 moves (4,7) -> (30,25)"**, and
  it genuinely pushes boulders -- (9,10) was observed moving to (10,10).
* `reset_floor` restores a half-pushed floor through each floor's own back
  door, verified by the run re-descending to a fresh B1F.

**Where it stops:** executing that 68-move plan. The walk consistently reaches
**(17,12)** and then reports `no solution to (30,25) from (17,12)` -- and from
the same spot no solution to B1F's other exit (42,25) either. Sokoban pushes
are one-way, so a plan that diverges midway (a battle interrupts, a push lands
differently) strands the floor; the reset then undoes it and the next attempt
diverges at the same place.

The fix is in execution, not search: run the whole plan without re-planning
after interruptions, or make the solver refuse pushes that reduce
connectivity. Both are `boulder_solver` changes, and the 68-move plan proves
the geometry is solvable.

Two smaller fixes worth keeping either way:
* Walking onto a warp tile FIRES it, so a changed map is arrival, not a stall.
  Treating it as a stall made the crossing reset the floor immediately after
  every successful descent.
* `reset_floor` took "whatever warp is reachable first", which was the warp it
  was trying to reach -- the reset walked into the puzzle it existed to undo.

## session port-43: Ever Grande reached; Victory Road is the next stage

`scripts/league_run.py` drives Sootopolis -> Ever Grande and up onto the
plateau, verified end to end on a fork of `badge8-won-real.state`:

* Mossdeep -> Route127 -> Route128 -> EverGrandeCity, one connection per hop.
* **The waterfall climb at (18,68).** From the sea only 320 cells are
  reachable and none of the city's three doors are among them. `nav` cannot
  answer this -- it models MB_WATERFALL as ordinary water, so it reports the
  plateau's doors as reachable from sea level and the first version of this
  script announced "ON THE PLATEAU" while sitting at (1,67) in the ocean. The
  climb is judged by POSITION now (`y <= 59`).
* `climb_waterfall()` needs two or three tries -- the first presses report
  "pressed A facing north but nothing moved" -- so the leg retries.
* Nav routes the surf dismount itself: from (18,56) the Victory Road door is
  reachable, and `take_warp(18,41)` lands in `VictoryRoad_1F (15,40)`.

**Victory Road traversal is NOT solved and is the next stage.** Three separate
attempts, all instructive:

* `travel("EverGrandeCity")` from inside returns True in under a second by
  walking back out the door it just came in (18,42).
* `reach_cell(39,5)` -- the north exit -- ran **41 minutes** without
  returning, fighting trainers the whole time.
* `reach_cell(9,14)` -- the stairs down, which the decoded grid puts in the
  SAME component as the entrance -- hung for 20 minutes too.

Land-only components say 1F splits into 476 / 188 / 1 cells with the north
exit (39,5) stranded in the 188 with (21,32), reachable only via B1F (20,21).
But those numbers ignore Surf, and the floors are boulder puzzles with
Strength sections, so the decoded grid claims reachability the engine refuses
-- precisely what `scripts/boulder_solver.py` was built for in the Seafloor
Cavern. The route to wire, floor by floor rather than one search across three:
1F (9,14) -> B1F (8,3) ... B1F (20,21) -> 1F (21,32) ... 1F (39,5) -> the
league side of Ever Grande.

## session port-42: **BADGE 8. Wallace beaten.** All eight, all HMs live

Checkpoint: `saves/badge8-won-real.state`. `FLAG_BADGE08_GET` True,
`can_waterfall()` True, money 130 -> 830 on the win.

The badge-8 wall was never one problem, and none of the three real ones were
tactics -- which is where four earlier sessions spent their time:

1. **The battle was not happening at all.** Sootopolis' floor cracks under
   every thin-ice tile you step off, so ordinary routing fell through to B1F
   halfway across. The loop logged "walk to Wallace failed (left
   SootopolisCity_Gym_1F for SootopolisCity_Gym_B1F mid-route)" for thirteen
   minutes straight and never once started a fight. `ice_run.cross_to()` is
   now called by `challenge_leader`.
2. **It was aimed at Wallace's own cell.** (8,2) is unreachable by
   definition, so the solver crossed all three sections to (8,6), failed the
   last step and reset the floor, over and over. It targets (8,3) now -- the
   tile in front -- which was `ice_run`'s CLI default all along.
3. **The run kept re-fighting at a strength that had already lost** (port-41).
   The retry bar forced training instead: party total 277 -> 285, and the
   rematch was won on the first attempt at the new strength.

Field moves are all live for the first time this run: CUT NINJA, FLY/SURF SEA
BIRD, STRENGTH/WATERFALL/DIVE LOTTAD, FLASH NINJA, ROCK SMASH MIGHTYENA.

**What this unlocks:** Waterfall, so Victory Road and the Elite Four are
reachable, and with them the only real XP on the board. The Pokedex was
supply-blocked on money and balls (port-40) and both are downstream of here.

Next: Victory Road -> Elite Four, then the fresh-save end-to-end replay.

## session port-41: stop losing on purpose, and anchor the widget

Two stage-quality fixes, both aimed at "perfect this stage before the next".

**The run was re-fighting a gym it could not win.** `next_objective` compared
LEAD level against the leader's ace and nothing else, so an L53 lead walked
into Wallace again and again while the other five sat at 42-49 with nothing
super-effective. Every loss halves the money, and money buys the balls the
Pokedex needs -- that is how this run drained to 62 and stopped catching.

A defeat now records the party TOTAL it happened at
(`saves/<run>.state.losses.json`) and the objective stays `train` until the
whole party is `RETRY_PARTY_GAIN` (8) stronger. Levelling only the lead does
not clear it, which is precisely the mistake being corrected. Two further
faults each disarmed the guard completely and are fixed with tests: the bar
was checked AFTER the "am I at the gym yet" branch, and the tables say
"Wallace" while callers say "WALLACE".

Live proof on the run's own save: the objective flipped from a fourth rematch
to `train: Wallace beat us at party total 277; need 285`, the loop left the
gym, and the total is climbing (277 -> 279 in nine minutes) at Cave of Origin.
It will re-challenge on its own at 285, and a further loss simply raises the
bar again -- a self-correcting climb rather than a money spiral.

**The widget flicker was reflow, not frames.** OPPONENT and STAGES exist only
during a battle, and Column skips invisible children entirely, so each
encounter resized the panel twice and dragged everything below the screen
image with it. `ReservedSlot.qml` keeps a high-water mark of a section's
height so the space stays reserved once seen, and the popup's own
`contentHeight` is a high-water mark too -- it grows to fit and never shrinks
back within a session. qmllint clean, hot-reloaded with no QML errors.

That is the third distinct cause found for this symptom, after the stale-feed
buffers and the racing frame writes (port-40).

## session port-40: four silent faults, and the supply spiral behind the dex

Every one of these produced *zero* observable output while looking busy, which
is the pattern worth remembering: **a loop that logs is not a loop that works.**

1. **An underwater save is a dead end.** Fly is refused on
   MAP_TYPE_UNDERWATER, so a save left in `Underwater2` broke every script
   that starts by flying. The Safari sweep failed its first move and
   restart-looped for twenty-five minutes while its log still showed an
   earlier run's `INSIDE` and `GIRAFARIG` lines -- it read like progress.
   `Driver.fly_to` now surfaces first (one `dive()` toggle). Verified live:
   Underwater2 -> LilycoveCity (24,15).
2. **The declined move-learn loop** (port-39) -- zero XP for twenty minutes.
3. **The collector shopping livelock** -- 5 balls, a 30-ball target and 70
   money sent it to the Mart and back forever, never reaching a hunt.
4. **Racing frame writes dropped frames** -- a third real flicker cause.

**The dex is supply-blocked, not search-blocked.** The targets are known and
reachable; what is missing is money and balls:
* Mossdeep's shelf is ULTRA 1200 / NET 1000 / DIVE 1000 -- there is no cheap
  ball in this region at all.
* The Safari Zone is the only bulk ball source (500 buys entry AND 30 balls)
  and it needs 500 up front.
* Losing to Wallace halves money, and Wallace cannot be beaten at L38-53.

One catch was also LOST to concurrent writers: a GIRAFARIG banked by the sweep
was overwritten when the collector saved the same `live-run.state` from its own
emulator. **One process per save file, always.**

## session port-39: the decline loop -- why nothing could ever level

**A declined level-up move never dismissed its second box.** Gen 3 asks twice:
"Delete an older move?" -> NO, then "Give up on learning X?" -> YES. The
harness answered only the first, so the engine walked back to box one and the
decline repeated forever. A sea grind logged `harness declined THIEF for
MIGHTYENA` thousands of times across twenty minutes in which every party
member gained **exactly zero** experience -- measured on `experience`, not
guessed from levels.

Fixed in `BattleSession._decline_learn` (NO then YES, bounded, stops the moment
`learn_prompt()` clears) with two unit tests, one of them a guard against the
unbounded retry. Immediately after: +3,630 XP on MIGHTYENA and +1,839 on
PELIPPER in five minutes, PELIPPER L41 -> L42.

This is the same family as gotcha 18 and the naming-keyboard bug: **any prompt
that re-arms turns a single answer into an infinite loop.** Three separate
instances have now cost this run real hours -- the mart YES/NO, the naming
keyboard, and now the learn prompt.

Also landed for the badge-8 push:
* `scripts/sea_grind.py` -- levels ONE named mon by switching it in on turn
  one, because wild XP only goes to participants and
  `PartyOrder.lead_with` returns False on this save (still open).
* **TM34 Shock Wave is now on PELIPPER in the LIVE save.** Every earlier teach
  landed on a scratch fork, so the run had been fighting a Water gym with no
  super-effective move at all.

## session port-38: Route131 is TWO seas, and the pillar sits in the far one

Mapped with `nav.reachable`, not guessed. Sky Pillar's door is `warp_event`
(36,6) on **water** (MB_UNUSED_DEEP_WATER), so no dismount is involved -- and
it is still unreachable by surfing from anywhere this run can stand:

* **Route131 splits into two water bodies.** Southern: 985 cells, everything an
  ordinary route lands in. Northern: 523 cells, bbox x4-59 y0-16, holds the
  door. A land bar at rows 8-13 separates them across the full width and they
  share no cell. `travel("Route131")` is satisfied by EITHER, which is why it
  kept reporting success on a map whose warp could never be approached.
* **Connection offsets here are all 0**, so latitude carries across a seam.
  The northern strip touches Route131's right edge at y0-13, which maps to
  Route130's northern body -- itself separate from Route130 south.
* **Chasing that corridor upstream dead-ends.** Route129 has a NW pocket
  (278 cells, left edge y0-7) that feeds Route130 north, and it is fed by
  Route128's WESTERN body (717 cells, top row x0-9) -- which Route127's main
  body never touches (its bottom row starts at x18). Every hop is a different
  component.
* **Routes 129/130/131 south are a closed system.** Nothing in it reaches
  Route128; its only exit is Pacifidlog. Landing there stranded the voyage and
  produced the endless 129->130->131->129 ping-pong in earlier logs.

The upstream corridor is closed too, and for the same reason. Route127's
western body (85 cells) meets Route126 only at rows 61-70, and Route126 is
ITSELF split: a 3151-cell main sea and a 194-cell southern pocket, divided by a
diagonal coastline around rows 59-67 that the two never cross. Rendered cell by
cell it is plainly land, not a modelling artefact -- and none of these maps
carry current behaviours (0x50-0x53) that could explain it away. Every link in
Route126 -> 127w -> 128w -> 129 pocket -> 130n -> 131n is a genuinely separate
body of water.

Read as geography rather than a bug: the lagoon is Sky Pillar's island, and in
Sapphire the pillar is normally opened by a scripted escort after the Kyogre
event. A future attempt should look for that trigger (or a current/Mach Bike
route) rather than more pathfinding.

`scripts/skypillar_grind.py` keeps the useful parts regardless: the ice-floor
gym escape, the crater fly-out (once, not per pass), per-leg banking, the
Center-door heal, and `sail_north` for latitude-controlled seam crossings.

## session port-37: the voyage to Sky Pillar reaches Route131 and stops there

`scripts/skypillar_grind.py` now gets most of the way. Four routing bugs fixed
on the way, each worth keeping:

* **A save on the gym ice cannot travel at all** -- it escapes to (8,25)/(9,25)
  with the ice solver first.
* **Sootopolis is a crater** -- it flies out, but ONLY from Sootopolis. Firing
  the fly-out on every pass teleported it back to Mossdeep and discarded the
  sea legs already walked; the log was pure "flew to MossdeepCity".
* **Pacifidlog is not a Fly landing on this save** (never visited), so Mossdeep
  is the exit and the heal.
* **Healing at a third abandoned the voyage.** Sea legs cost HP, so arriving at
  Route131 always tripped the threshold and it flew back and re-sailed forever.
  While travelling it now pushes on unless the lead is under a sixth.

**Where it stops:** on Route131, the pillar's doorstep. Two things to chase --
`could not cross the L seam to Route131` from Route130 (an intermittent seam
crossing, the same family already fixed for the Mossdeep sea route), and then
the (36,6) door itself, which must be entered from land while the player is
surfing. `scripts/cavern_run.py`'s explicit-hop style is the pattern that
worked for exactly this shape of problem.

Worth it because Sky Pillar 1F is L47-50 at a 10% rate with CLAYDOL and BANETTE
new to the dex -- the only XP on the board that can lift this party out of the
badge-8 hole.

## session port-36: Sky Pillar is the right XP and the router cannot get there

Sky Pillar 1F is **L47-50 CLAYDOL / BANETTE / GOLBAT / SABLEYE at a 10% rate**
and is NOT gated -- only the crumbling floors on 2F and 4F want the Mach Bike.
It is far and away the best XP reachable on seven badges, and CLAYDOL and
BANETTE are new dex entries on top. `scripts/skypillar_grind.py` drives it.

Three routing facts were learned getting there, each now encoded:
* **A save left on the gym's ice floor cannot travel anywhere.** Routing will
  not cross the ice, so every `travel()` failed on its first move and the grind
  logged "could not climb (at SootopolisCity_Gym_1F)" forever. It escapes to
  (8,25)/(9,25) with the ice solver first.
* **Sootopolis is a crater**; its only walking exit is a dive, so it flies out.
* **Pacifidlog is not flyable on this save** -- never visited, so
  FLAG_VISITED_PACIFIDLOG_TOWN is clear and the Fly map greys it out. Mossdeep
  is the nearest unlocked landing.

**Where it stops:** from Mossdeep, `travel("Route131")` cannot plan the open-sea
route to the pillar. That is the remaining gap -- Route 131 sits past Pacifidlog
in the south-west and the router will not cross that much water in one plan.
Either seed a Pacifidlog visit (making it flyable, one short trip) or walk the
sea route in explicit hops the way `scripts/cavern_run.py` does.

**Levels are moving anyway.** The general play loop took the party from
[37,38,42,44,48,41] to [39,40,44,44,48,41] while all this was going on, which
makes it -- not a bespoke grinder -- the thing to leave running.

## session port-35: X ATTACK reaches +6 and the battle loop WEDGES there

Tried the standard answer to a Recover stall: stack X ATTACK (500 at Sootopolis)
and swing a neutral physical move. It half-worked and then exposed a harness
bug worth more than the attempt.

**What worked:** the shop bought X ATTACKs, the breaker policy stacked them, and
the engine's own refusal proved the cap was reached --

    [battle] used X ATTACK but the bag still holds 6 of it -- the engine did
             not accept the item, and the turn was not spent

That message only appears at +6, so the Attack multiplication happened.

**What broke:** after `_execute` retired the maxed-out action
(`('item','X ATTACK') changed neither side's HP twice -- retiring it`), the
battle produced **no further turns for eighty minutes** against WHISCASH at
154/154. The loop kept the process alive and printed nothing. So the stall
detector retires an action correctly and then has nowhere to go when the
remaining choices are also refused -- it neither escapes nor falls back.

Two smaller findings:
* `lead_with("MIGHTYENA")` did not take -- the fight opened with LOTTAD
  (STRENGTH). Worth checking before trusting a lead in a scripted battle.
* `me["stat_stages"]` is not keyed `attack`/`atk` the way the policy assumed,
  which is why it kept buying stages past four instead of stopping at the
  intended stack.

**The strategic conclusion is unchanged and now doubly evidenced: this roster
cannot beat Wallace.** Even with Attack at +6 it could not put WHISCASH below
154. Levels first; everything else is a workaround for being underlevelled.

## session port-34: badge 8 is a DAMAGE problem, proven -- not items, not tactics

`scripts/badge8_run.py` now does the whole approach end to end and it works:
leave the gym, fly/walk to Sootopolis Mart, read the shelf, buy 30 HYPER POTIONs
(45,884 -> 17,004), heal, re-cross all three ice sections, and open the fight
with SEA BIRD leading. Two full runs got to Wallace with 31 potions in the bag.

**Both ended `outcome: stalled`, and that word is precise**: the battle module
declares it after *four consecutive turns in which neither HP bar moved*
(`pokeagent/battle.py:1864`). We heal, Wallace heals his own mon, nothing
advances. Raising the frame budget from 200,000 to 4,000,000 changed nothing,
which rules out "the battle just needed longer".

So the ceiling is damage output, and the arithmetic is not close: a L42
Pelipper's SHOCK WAVE -- 60 base, Electric, 2x on everything Wallace owns, and
the best answer this roster has -- lands well under what MILOTIC's Recover puts
back. **No quantity of potions wins a race you lose every turn.**

Things tried and eliminated tonight, so nobody repeats them:
* Leading with LOTTAD (Water/Grass, 0.25x from Water) -- dies to Ice moves.
* Leading with ROCKY (L48, the highest level) -- Water is 4x on Steel/Rock.
* A sustain policy healing at 40%, then at 60% -- fires correctly now, and
  simply feeds the stalemate.
* Buying out the Mart -- 31 HYPER POTIONs, same result.

**What actually remains is levels**, and the cruelty of it is that the good XP
(Victory Road, Meteor Falls' back room) is behind WATERFALL, which needs
FLAG_BADGE08_GET. The way out is trainer XP, not wild XP: the Sootopolis gym's
own trainers stand on the ice sections `scripts/ice_run.py` already walks, and
every unbeaten trainer elsewhere in Hoenn is worth more than a Magikarp.

Useful discoveries along the way:
* `battle_frame()['bag']` is nested BY POCKET (`items`, `key_items`,
  `poke_balls`, `tms_hms`, `berries`). Reading it flat is why an earlier heal
  branch never fired once.
* The bag holds **TM34 SHOCK WAVE**, taught to SEA BIRD (over WATER GUN) -- the
  only super-effective move this team has ever had against Water.
* Sootopolis Mart stocks FULL HEAL, HYPER POTION, REVIVE, X ATTACK, X DEFEND,
  ULTRA/NET/DIVE BALL -- but **no X SPECIAL**, so the obvious burst fix for a
  special attacker is not purchasable there.

## session port-33: the dex is blocked on MONEY and BALLS, not on finding species

The catching machinery works now (28 -> 39 tonight). What stops it is a supply
chain, and it fails in three linked places. Two are fixed; the third is the next
session's first job.

**Fixed:**
* `route_legs` cannot plan INTO an indoor map, so from Route115 it answered None
  for all eleven marts and the collector parked on "no Mart in reach; 0 balls".
  `nearest_mart` now falls back to any mart whose town is a Fly destination.
  Verified: Route115 -> `OldaleTown_Mart` instead of None, and it flew there.
* Ball selection matched a hardcoded (POKé BALL, GREAT BALL, ULTRA BALL)
  list against the ROM's own strings, which does not survive the accented byte.
  At Oldale it asked for an ULTRA BALL the shop does not stock while a
  POKé BALL sat on the shelf. It now reads the shelf and takes the cheapest
  thing with BALL in the name.

**STILL BROKEN -- start here.** After both fixes the log is still

    balls 0 -> 0 (asked for 30, buy=False)

with no "buying Nx" line at all, which means the shelf loop found nothing --
i.e. `self.mart.items()` is returning empty inside the collector. It is clearly
readable, because the mart module itself printed the real stock in its own
refusal message:

    [mart] ULTRA BALL is not sold here (stock: POKé BALL, POTION, ANTIDOTE,
           PARLYZ HEAL, AWAKENING)

So the data is there and `Collector.restock_balls` is not getting it. Fix that
and catching resumes immediately.

**The other half of the same problem is money: 1,262.** Repeated whiteouts
halve it, and the party is too weak to farm trainers. Levels and money are the
same blocker wearing two hats, and both are downstream of badge 8.

## session port-32: Safari Zone reached; dex 39 and climbing unattended

`scripts/collect.py` reported `could not reach SafariZone_Northwest` -- its
router has no idea how to pay a 500 entry fee -- but `scripts/safari_probe.py`
already knew how, from an earlier session. Pointed at the live save it walked
straight in (`INSIDE: SafariZone_Southeast (32,35) | balls 30 steps 500
in_safari True`) and caught GIRAFARIG, "new to the Pokedex".

**Running now on a 300-minute budget.** Heracross, Pinsir, Phanpy, Natu, Xatu,
Pikachu, Wobbuffet, Doduo, Dodrio and Rhyhorn all live behind that gate, so this
is the densest block of missing species reachable on seven badges.

Caveat for the next session: money is down to ~1,262 and each entry costs 500,
so the sweep has two more visits in it before it needs cash. The two tools are
complementary and neither subsumes the other:

* `collect.py` -- plans over walkable ground, fishes, paces. Cannot dive, cannot
  pay the Safari gate.
* `dive_sweep.py` -- Underwater1/Underwater2 only (Clamperl, Chinchou,
  Relicanth at a 4% rate).
* `safari_probe.py` -- the Safari Zone, including its own ball/step accounting.

## session port-31: fishing actually casts now -- dex 28 -> 38

Three faults were making every fishing pass a no-op, and fishing is the highest
-yield method left (Sharpedo, Luvdisc, Corsola, Horsea, Staryu, Carvanha and
Corphish are all super-rod-only and all reachable on seven badges):

* **Aiming walked into the water.** The spot picker routed to a shore cell and
  then used `step_dir` to face the water -- which HOLDS the key and steps when
  the tile is open. So it moved onto the cell it had just reached and cast at
  floor: `wrong-tile: (17,45) facing U is floor (behavior 0x0)`, forever. A
  short tap turns in place.
* **`cast-failed: the bag would not USE SUPER ROD ... START would be
  swallowed`** is an unanswered box. `close_menus()` fixes the waiting kind; a
  scene still RUNNING holds `sLockFieldControls` and has to be walked to its
  end first.
* And the collector could not even launch (see below).

**Measured: 28 -> 33 caught in the first fixed pass, 33 -> 38 in the five
minutes after the aiming fix.** It is running unattended on a 600-minute budget.

Still open: `could not reach SafariZone_Northwest` -- the Safari gate is not
something the router knows how to pay for, and Heracross, Pinsir, Phanpy, Natu,
Girafarig, Pikachu and Wobbuffet live behind it.

## session port-30: the dex collector is unblocked and climbing

**CAUGHT 28 -> 32 in ten minutes**, and it is still running (600-minute budget,
`scripts/collect.py --state saves/live-run.state`). It had been dead in the
water for three separate reasons, all now fixed:

1. **It could not launch at all** against the live save. A Driver opened on a
   path under `saves/` already attaches a LiveFeed publishing to the exact name
   the widget watches, and a second attach raises outright. `play.py` had been
   taught to reuse the existing feed; `collect.py` never was.
2. **The naming keyboard ate every catch.** Declining a nickname fell through to
   TYPING the species name, on a cursor this ROM will not always move, and each
   failure re-offers the prompt. Now it accepts the pre-filled buffer, which is
   instant and cannot fail.
3. **It had no Super Rod.** Now it reports `rods {'super_rod','good_rod',
   'old_rod'}` and 158 missing species, which is what makes fishing worth doing
   -- Sharpedo, Luvdisc, Corsola, Horsea, Staryu, Carvanha and Corphish are all
   super-rod-only and all reachable without another badge.

`scripts/dive_sweep.py` remains the tool for the two underwater fields the
collector cannot route to (it plans over walkable ground and cannot dive);
Underwater1/Underwater2 are Clamperl/Chinchou/Relicanth at a 4% rate, so run it
deliberately rather than expecting the collector to find them.

## session port-29: the underwater sweep exists, and the naming loop is dead

`scripts/dive_sweep.py` drives the descent the collector cannot -- it plans over
walkable routes and has no notion of diving -- and hands each encounter to the
collector's own `Catcher`. Verified live: dived at Sootopolis (25,54), swam to
`Underwater2`, and met CHINCHOU with "new to the Pokedex".

Two routing facts it encodes:
* `Underwater1` (dive on Route 124) and `Underwater2` (Route 126) are the ONLY
  underwater maps with encounter tables -- Clamperl 65%, Chinchou 30%,
  Relicanth 5%, at a **4% rate**, so this is slow by design.
* Sootopolis is a crater. Its only exit is a dive, and the basin directly under
  it has no encounter table at all, so the sweep has to swim ON to the field
  that does.

### The naming keyboard was eating the sweep
`handle_nickname`'s docstring promises that declining takes the species name and
"cannot fail". The code did the opposite: with no nickname it fell back to
`name = species[:10]` and **typed** it, walking a cursor this ROM will not
always move. Every failure re-offers the prompt, so the loop names the same mon
again -- twenty minutes of sweeping produced one catch and a log that was
nothing but

    [battle] named the catch 'CHINCHOU'
    [battle] could not type 'CHINCHOU' (could not move the cursor to 'C' at (2,0))

The engine pre-fills the buffer with the species name, so `kb.accept()` is
instant and cannot fail. That is what the comment always claimed happened.

### Also this session
`Teacher.slot_to_forget` now spends a **duplicated type** before a weaker sole
type. Ranking on raw power made LOTTAD forget ABSORB (20) for THIEF (40) --
throwing away the party's only Grass move on the way into a Water gym, with
FAKE OUT sitting in the next slot duplicating STRENGTH. 615 unit green.

## session port-28: the ice floor is solved -- Wallace is one trained team away

**612 unit + 39 integration green.** Milestones: `saves/ice-crossed.state`
(standing on (8,3), face-to-face with Wallace, all three ice sections walked).

### The Sootopolis ice floor, solved
`scripts/ice_run.py` crosses all three sections. The puzzle is NOT "avoid the
cracked tiles" -- it is coverage: `VAR_ICE_STEP_COUNT` gates the three stair
sets at 8 / 28 / 69, and until a section's threshold is met its exit is an
`MB_SLIDE_SOUTH` tile that shoves you back and drops you to Gym_B1F. Measured
live: three ice steps, then north onto (8,16), then Gym_B1F with the counter
zeroed.

So each section needs a **Hamiltonian path over its ice**, and four things had
to be right before it worked:
* A closed stair is a WALL, not floor. Treating it as floor let the router plan
  a 22-move stroll straight to Wallace, four times over.
* The path must **end beside the stairs**. Section 1 covered perfectly and
  stranded the run on (7,18), ringed by its own holes, counter already past the
  threshold.
* Only some entries admit a covering path: section 1 from (8,19) but not
  (9,19); section 2 from (6,12) or (8,12) and no other; section 3 from (8,9).
* The search needs **nodes** (50M) with a wall-clock cutoff (8s) doing the
  bounding. At 400,000 nodes the 40-tile section reported "no covering path"
  with a 39-move answer three seconds away.

Result, live: `section 0: 7 tiles -> ice=9`, `section 1: 19 tiles -> ice=29`,
`section 2: 40 tiles`, then a clean walk to (8,3).

**And the bug that hid all of it:** `read_floor` only marked cells INSIDE the
grid as walls, so the floor BFS wandered off the map into unbounded empty space
and never returned. Three runs sat silent for twenty-five minutes each at
exactly the point where they had just chosen an entry. Sealing the border made
the same work take seven seconds.

### Where the run actually stands
Wallace was fought and **lost**. That is not a harness problem: the team is
L36-48 and still swinging CUT and HEADBUTT as attacks, against a 147 HP
Milotic. Getting into the gym also needed two fixes worth keeping: the door
(31,32) is a metatile with collision 1 so nothing can stand on it (approach
(31,33) and walk UP), and the escort parks **Steven** on that approach -- a map
reload puts him straight back, but talking to him walks him off.

### Badge 8 attempted twice more, lost twice -- and why that is a TEAM problem
Second attempt led with LOTTAD, which is Water/Grass and therefore takes 0.25x
from every single thing Wallace owns, now carrying WATERFALL (STAB). It reached
Seaking, the fourth of five, and ran out. The roster is simply short of levels:

    LOTTAD 37  MIGHTYENA 38  EMBER 42  NINJA 44  ROCKY 48  SEA BIRD 41

and not one of them has a move that is super-effective against Water. LOTTAD
even traded ABSORB away for THIEF on a level-up, which the default learn policy
allowed because THIEF is also a damaging move.

**HM07 WATERFALL is now taught** (LOTTAD), so all eight HMs are usable -- except
that `can_waterfall()` is False until badge 8: `field_control_avatar.c:511`
requires FLAG_BADGE08_GET. That closes the obvious training loop, because the
good XP (Victory Road, Meteor Falls' back room) is behind the waterfall climb
that badge 8 unlocks. Sootopolis' own lake is Magikarp and Tentacool -- 20
battles moved nobody a single level.

**So the next session's first job is XP, and the cheapest source is not wilds:**
the Sootopolis gym's own trainers sit on the ice sections the solver already
walks, and Victory Road opens the moment badge 8 lands. A Move Reminder visit
(Fallarbor, Heart Scale) would also give Blaziken back a Fighting move -- Sealeo
is Ice/Water and takes 2x from it.

**Next, in order:**
1. **Train.** The party needs levels and real moves before badge 8 is winnable.
   TM04 CALM MIND and TM03 WATER PULSE are already in the bag; ABSORB on
   LOTTAD is the only super-effective move the team owns against Water.
2. Re-cross the ice (`scripts/ice_run.py`, ~4 minutes) and rematch Wallace.
3. Then WATERFALL is already in the bag: Ever Grande, Victory Road, the Elite
   Four, and the whole post-game dex behind them.

## session port-27: cavern cleared, Kyogre beaten, HM07 taken, gym door open

**612 unit + 39 integration green**, clean tree, everything pushed. Milestones:
`saves/seafloor-cleared.state`, `saves/kyogre-done.state`,
`saves/hm07-waterfall.state`, `saves/sootopolis-healed.state`,
`saves/sootopolis-gym.state`.

### Landed
* **Seafloor Cavern cleared end to end** -- `VAR_SEAFLOOR_CAVERN_STATE=1`,
  `VAR_SOOTOPOLIS_STATE=1`, `VAR_ROUTE128_STATE=2`.
* **Kyogre beaten** -- `FLAG_LEGENDARY_BATTLE_COMPLETED`, which is what unlocks
  the city doors and the gym.
* **HM07 WATERFALL collected** (CaveOfOrigin_B3F (6,5)). All eight HMs are now
  in the bag and seven field moves are live on the party.
* Sootopolis reached, escorted, healed; **inside the gym**.

### Nine more bugs, every one found by playing
1. **Water currents are forced movement.** Room6 is built from
   `MB_*_CURRENT` (0x50-0x53); nav read them as plain water and the engine
   refused the same step twelve times. Crossed in **5 seconds** once modelled.
2. **A boulder cannot be pushed into a breakable rock** -- the player may walk
   into one and smash it. Without that asymmetry a solvable room reported "no
   solution" after 4,000,000 states.
3. **A boulder cannot be pushed onto a WARP** either. Room8 puts one on (5,3),
   the only approach to its door.
4. **`nav.info().warps` silently returned nothing** -- `Warp` is a dataclass and
   `w["x"]` raised inside a bare `except`.
5. **Boulders reset on map reload**, so a wedged room is always recoverable --
   and the solver's belief must be dropped with it.
6. **`live_npcs()` only reports objects near the camera**, so plans were built
   against two of Room2's seven boulders.
7. **A dry moveset must press on for STRUGGLE.** The Seafloor Cavern boss burned
   two 25-minute runs taking *zero* turns: the policy declined, the switch
   verifier rejected its own SHIFT, and fleeing is refused in a trainer battle.
8. **SHIFT verification read `gBattlerPartyIndexes[0]` once, immediately.**
   Wrong in doubles and too early in singles.
9. **An unanswered description box eats every movement press** -- a failed
   HYPER POTION left one open and a walk replanned the same three moves for
   fifteen minutes.

Widget: the frames were always clean; the blank preview was the screen box
never being told to reload (the URL only changes when the frame counter does,
and the emulator does not tick while a route is planned). It now self-heals on
a 1.5s timer.

### The one thing left for badge 8: the ICE FLOOR
`SootopolisCity_Gym_1F` is the cracked-ice puzzle. Each tile cracks when
stepped on and stepping on a cracked one drops you through a `warphole` to
`Gym_B1F` -- which is exactly what happens now: the run enters at (8,25),
walks toward Wallace at (8,2), and falls. `VAR_ICE_STEP_COUNT` gates the three
stair sets at 8 / 28 / 69 steps (`SootopolisCity_Gym_1F/scripts.inc:37-73`).

This is the same shape as every puzzle solved this session -- a forced/one-shot
tile model plus an offline search -- and the pieces already exist:
`scripts/slide_probe.py` validates a movement model against the emulator, and
`scripts/boulder_solver.py` shows the pattern (model the rule the decomp
states, plan offline, verify each step). The state to model is
(position, set of already-cracked tiles).

Getting into the gym itself needed two fixes worth remembering: the door
(31,32) is a metatile with collision 1 so nothing can stand on it (approach
from (31,33) and walk UP), and the escort leaves **Steven** parked on that
approach -- a map reload puts him straight back, but talking to him walks him
off.

## session port-26: badge 7 won, the Seafloor Cavern half-crossed, nine harness bugs

**611 unit + 39 integration green** (three lane runs in a row), clean tree,
everything pushed. Milestones: `saves/badge7-won.state`, `saves/badge7-items.state`,
`saves/cavern-room6.state`.

### What the run gained
* **BADGE 7** (Tate & Liza). `scripts/mossdeep_solver.py` solves the gym floor
  offline in **0.48s** and walks it in 92; `Driver.damage_first` won the fight in
  **16 seconds** after the scored tactics had lost it twice from a winning
  position.
* **HM08 DIVE** and the **SUPER ROD** collected. DIVE taught to LOTTAD -- all
  seven field moves usable for the first time in the run.
* Into the Seafloor Cavern and through Room1 -> Room2 -> Room6, which needed
  four separate fixes to become possible at all.

### Nine bugs, each found by playing and each verified live
1. **Doubles were unplayable.** The replacement check read
   `gBattlerPartyIndexes[0]`, so a mon sent to the right-hand slot was declared
   missing and the battle reported `stuck`.
2. **The tactics threw winnable fights** on SAND-ATTACK and resisted moves.
   `Driver.damage_first` is now a first-class policy with tests.
3. **Surfacing pressed the wrong button.** `TrySetupDiveEmergeScript` is gated
   on `pressedBButton` (field_control_avatar.c:233); descent is A at :521.
4. **`Driver.close_menus` did not exist**, though `dive()` and
   `climb_waterfall()` both call it on their refusal paths -- proof those paths
   had never run.
5. **Strength had never been used.** `FIELD_OBSTACLES` named
   `S_PushableBoulder -> STRENGTH` but nothing could push one.
6. **A search can never arrive ON a warp** -- the step that reaches it changes
   the map, so the winner test cannot fire. Room1 solved in 25 steps and still
   reported failure.
7. **`live_npcs()` only reports objects near the CAMERA** -- two of Room2's
   seven boulders on arrival -- so plans walked into boulders the model could
   not see.
8. **A boulder cannot be pushed into a breakable rock**, though the player may
   walk into one and smash it. Missing that asymmetry made a solvable room
   report "no solution" after 4,000,000 states.
9. **Boulders reset when the map reloads** (verified: (7,11) -> (5,11) after one
   round trip), so a wedged room is always recoverable -- and the solver's
   belief must be dropped with it.

### The widget
* Flicker: the frames were never the problem (399 reads at 20 Hz, every one a
  complete 240x160 PNG). Three varying-height lines ABOVE the framebuffer were
  dragging it several times a second; all now reserve their space.
* Blank preview: the buffers only load when the URL changes, and the URL only
  changes when the frame counter does -- so a panel opened while the driver was
  planning a route (the emulator is not ticking) sat empty until the game moved.
  A 1.5s timer now re-arms the back buffer whenever the front is not Ready.
* **Staleness has one cause worth remembering: only a Driver opened on a path
  under `saves/` publishes.** Driving forks in `.scratch/` feeds nothing. Long
  chains must run on `saves/live-run.state`.

### Where it stands and what is next
The live run is in `SeafloorCavern_Room6` at 7/8 badges, 28 caught / 95 seen.

### Cavern addendum (end of session): Room6 solved, Room3 is the wall

Room6 turned out to be a **water current maze** -- `MB_*_CURRENT` 0x50-0x53,
which nav reads as ordinary water. That is why `goto` planned a straight line
and the engine refused the same step twelve times at (14,16): you cannot swim
against a current any more than you can walk against Mossdeep's arrows. Adding
the currents to the (already emulator-validated) forced-movement model crossed
the room in **5 seconds**.

**Room3 is where it stands.** Nine boulders, no rocks. Facts, all measured:
* From the arrival cell **(4,15) it solves in 23 moves in 3 seconds**.
* From **(6,14) it has no solution at all** -- 12,000,000 states, exhausted.
* `use_strength()` is what puts the player on (6,14): it walks up to a boulder
  to press A, and that walk is itself a move that strands the run.
* `scripts/room3_run.py` exists to fix exactly this -- it picks an activation
  cell reachable without pushing anything that still leaves (8,2) solvable --
  but the live save was already sitting on (6,14) when it ran, so it has not
  yet had a fair attempt.
* Resets work: leaving via (4,15) to Room6 and returning restores all nine
  boulders to their map defaults.

**The lead is fainted (SEA BIRD 0/124) and the party is inside the dungeon.**
Heal before anything else; `saves/badge7-items.state` is the clean fallback
(Mossdeep, 7 badges, DIVE + Super Rod, nothing spent).

**Next, in order:**
0. Heal, then run `scripts/room3_run.py` from a FRESH Room3 arrival: walk to
   (4,15), warp to Room6, cross with `scripts/room6_run.py`, come back through
   (4,1), and only then arm Strength.
1. **Room6 is flooded** -- its arrival ledge is six cells and the rest is
   `MB_OCEAN_WATER`. `_surf_sync()` opens 346 cells, but `goto(4,2)` then
   **stalls 12x at (14,16)**. That is the one live blocker; everything past it
   is scripted in `scripts/cavern_run.py` (Room6 -> Room3 -> Room8 -> Room9).
2. Room9 (17,42) triggers the Aqua leader and Kyogre wakes; the chain then runs
   Sootopolis -> Cave of Origin (**HM07 WATERFALL at B3F (6,5)**) -> Wallace.
3. Then the dex: the Super Rod species are reachable NOW, and Underwater1/2
   (dive on Routes 124/126) are the only underwater maps with encounters at all
   -- Clamperl 65%, Chinchou 30%, Relicanth 5%. Relicanth + Wailord are the
   Regi key.

## session port-25: BADGE 7 -- Mossdeep solved offline, and five harness bugs it hid

**611 unit green** (5 new), 39 integration green, clean tree, everything pushed.

### Badge 7 is won
The gym floor is 173 forced-movement tiles whose directions four switches
re-point. Two savestate searches (7,480 and 11,857 nodes, ~65 minutes) had found
nothing, because walking cannot flip a switch and `talk_to` cannot cross a slide.

`scripts/mossdeep_solver.py` solves it as a graph over (position, switch flags)
in **0.48 seconds** and walks the 47-move answer in **92**, pressing switches 2,
3 and 1 -- the ones nothing could reach before. Then `Driver.damage_first` took
the badge in **16 seconds**.

The model is measured, not guessed: `scripts/slide_probe.py` compared it to the
emulator transition by transition -- **91 of 92 landings**, the one disagreement
a trainer's sight line. It found what every earlier attempt got wrong:

* `MB_WALK_*` (0x40-0x43) force movement exactly like `MB_SLIDE_*` (0x44-0x47).
* `MB_TRICK_HOUSE_PUZZLE_8_FLOOR` (0x48) does **not** stop a slide -- which is
  why stepping LEFT at (2,22) lands on (8,17). A whole session read that as a
  teleport.
* NPCs block: the "walls" at (2,24) and (9,17) are gym trainers standing there.
* `grid_drift()` compares collision and elevation only, so a switch that merely
  re-points an arrow is invisible to it. Behaviour drift is real drift.

### Five harness bugs, all found by playing
1. **Doubles were unplayable.** The replacement check read
   `gBattlerPartyIndexes[0]`, so a mon sent to the right-hand slot was declared
   missing and the battle reported `stuck`. `sent_out()` asks every battler.
2. **The tactics threw the fight.** Left alone it spent turns on SAND-ATTACK,
   a resisted EMBER and HEADBUTT and lost twice with Lunatone on 2 HP.
   `Driver.damage_first` is now a first-class policy.
3. **Surfacing pressed the wrong button.** `TrySetupDiveEmergeScript` is gated
   on `pressedBButton` (field_control_avatar.c:233); descent is A at :521.
   Every attempt to come up reported "the map did not change" on a perfectly
   surfacable tile. Crystal gotcha 19, again.
4. **`Driver.close_menus` did not exist**, though `dive()` and
   `climb_waterfall()` both call it on their refusal paths -- proof those paths
   had never once run.
5. **Strength had never been used.** `FIELD_OBSTACLES` named
   `S_PushableBoulder -> STRENGTH` but nothing could push one, so the Seafloor
   Cavern's two-boulder corridor was impassable. `use_strength()` drives the
   game's own script and `reach_cell` now escalates boulder rooms with
   `boulder_signature()` in the node key.

### Also
* HM08 DIVE and the SUPER ROD collected; DIVE taught to LOTTAD. All seven field
  moves are available for the first time in the run.
* **A search can never arrive ON a warp** -- the step that reaches it changes the
  map, so the winner test cannot fire. Room1 solved in 25 steps and still
  reported failure. Doors are approached by a neighbouring cell now.
* Widget flicker: the frames are clean (399 reads at 20 Hz, every one a complete
  240x160 PNG). It was layout -- three varying-height lines ABOVE the
  framebuffer dragging it several times a second. All three now reserve space.
* **The widget goes stale when nothing opens a save under `saves/`.** Driving
  forks in `.scratch/` publishes nothing by design. Long chains run on
  `saves/live-run.state` for this reason.

**Next:** the badge-8 chain (`scripts/badge8_chain.py`) is mid-Seafloor-Cavern.
After it: HM07 WATERFALL at CaveOfOrigin_B3F (6,5), Wallace, then the dex sweep
-- Super Rod species are reachable now, and Underwater1/2 (dive on Routes 124
and 126) are the only underwater maps with encounters at all.

## session port-24: Mossdeep's gym is a TELEPORT floor, and three primitives were lying

**606 unit green.** The badge-7 story chain landed last entry; this one is about
why the gym itself still has not.

### The gym floor teleports you
Measured on a fork, from the entrance: one LEFT press moved the player from
(2,22) to **(8,17)**. Mossdeep's gym is several components joined by tiles that
look like ordinary floor, so nav's static grid reports the leaders reachable
across 394 cells and every escalation in `challenge_leader` is skipped -- all of
them ask nav FIRST. The plain `talk_to` then walks 360 chunks without arriving,
and the loop logs "challenging TateAndLiza at (8,3)" / "not beaten yet" forever
with the player never leaving (9,29).

Fixed the asking order (escalate when the WALK fails, not when nav predicts it
will) -- the same lesson the rotating gates taught, which this function had
learned only for gates. The floor itself is still unsolved: `solve_warp_maze`
does not identify these tiles as springs, so `reach_cell(8,4)` honestly returns
False. **That is the next concrete piece of work**, and it is a known puzzle
class: search over savestates with the emulator as the transition function.

### Three primitives reporting success they had not achieved
1. **`reach_cell` returned True while standing somewhere else.** From the gym
   entrance it answered True for (8,4) with the player at (2,23) -- its own
   reason string already read "walked 360 chunks without arriving". Everything
   above it believed it was beside the leaders and spent its talk on empty
   floor. Every success path is now gated on actually standing on the cell.
2. **`take_warp` counted ANY map change as the warp.** Drifting onto Route 134
   and crossing a map connection read as Slateport's harbour door opening.
   Held to the destination the map data names now.
3. **`travel(on_battle="fight")` did not reach `_cross_seam`'s `goto`,** which
   defaults to RAISING -- so a wild encounter mid-seam killed the journey:
   "a battle started at Route124 (62,25)". Sea routes are wall-to-wall
   encounters and every Mossdeep trip crosses two, so the journey could almost
   never finish. Third instance of this family today, after `take_warp` and the
   badge chain's own approach walk.

With that one fixed, the crossing to Mossdeep gym now takes **34 seconds** and
had been failing outright.

### The heartbeat
Route planning is pure Python, so the emulator does not tick and the widget
holds its last frame -- measured at 137 seconds. `Driver.heartbeat` writes a
narration line and publishes, once per travel and once per leg, so a thinking
run reads as "leg 3 to MossdeepCity from Route124 (79,50)" instead of looking
dead. Ruled out on the way: a savestate `load()` does NOT detach the feed.

### Standing
- Story: Mt Pyre, Stern and the submarine all done
  (`saves/badge7-chain-done.state`).
- **Badge 7 blocked on the gym's teleport floor.** Everything up to the gym
  door works and is fast.
- Dex 28. Still gated behind DIVE, WATERFALL and the SUPER ROD.

### Mossdeep gym: what is known, and what to do next (port-24 addendum)

The floor is **173 MB_SLIDE_* tiles** -- 0x44 EAST, 0x45 WEST, 0x46 NORTH,
0x47 SOUTH -- i.e. the whole room is arrows, and the four
`FLAG_MOSSDEEP_GYM_SWITCH_*` re-point them. Not teleporters: I checked
`MB_MOSSDEEP_GYM_WARP` (0x0E) and the grid contains ZERO of them; the map
declares exactly two warp_events, both the exit.

**The one hard measurement:** standing at (2,22), pressing LEFT lands the player
at **(8,17)** -- six columns across and five rows up. The tile entered, (1,22),
is 0x46 SLIDE_NORTH.

**A naive chain model is wrong.** I implemented "ride the arrow, re-read the
next tile, repeat" and it predicts (3,14) for that step. It also makes the
leaders look reachable in 18 steps, which is exactly the kind of confident
wrong answer that has cost this project days -- so it was REVERTED rather than
shipped. `git show` it if you want the code; it is not in the tree.

**Measured since:** with the node keyed on (map, position, switch flags) the
search explored **7,480 nodes and found no solution**. That is not a budget
problem, it is a MOVE-SET problem: the search's only moves are the four steps,
and Mossdeep's arrows are re-pointed by switches you PRESS. No amount of walking
can toggle one, so the reachable set is whatever the current switch
configuration allows -- and from the entrance, with all four flags clear, that
does not include the leaders.

An earlier run of the same search "succeeded" in 555 nodes, and that was a bug
worth naming: the node key was position-only, so it walked out the gym door and
called (8,4) of `MossdeepCity_House1` the target. The map is part of the node
now, and the arrival test checks it -- the honest answer is False.

**The switches are found and they flip.** They are `bg_events` of type "sign"
-- solid, so you press A facing them, which is why `floor_switches` (coord_events
only) returned an empty list and `press_floor_switches` was a silent no-op at
this gym. Both kinds are read now, and pressing is proven live from the
entrance:

    press (5,24):  switches (F,F,F,F) -> (F,F,F,True)
    press (17,15): switches (F,F,F,F) -> (F,F,True,F)
    press (2,7), (8,10): unreachable from the entrance

So the puzzle is exactly what it looks like: two switches are reachable at the
start, flipping them re-points the arrows and opens the way to the others.

**Latest measured state (end of port-24).** The two halves ARE joined now and
the mechanism is clean:

    pressed (5,24)  from (5,25): (F,F,F,F) -> (F,F,F,True)
    pressed (17,15) from (17,16): (F,F,F,True) -> (F,F,True,True)
    round 0: switches (F,F,True,True), at (1,7)
    round 1: switches (F,F,True,True), at (1,7)   <- no further progress

Three things that took a run each to learn, all now fixed in
`scripts/mossdeep_badge.py`:
* `talk_to` can NEVER press the deep switches -- it routes with nav, and nav has
  no model of a slide floor. Across eight switch plans, (2,7) and (8,10) were
  never pressed in any configuration. The savestate search reaches them because
  it asks the game.
* A Gen-3 `bg_event` sign is read from the tile BENEATH it facing up. The search
  reached (1,7), immediately west of switch (2,7), pressed A three times, and
  the flag never moved. Neighbours are tried below-first now, and the two
  reachable switches then pressed first time.
* Inside a pocket the search exhausts its frontier in **5 nodes**. The room
  really is small components joined only by presses.

**The one thing left:** the outer loop presses GREEDILY -- everything reachable,
every round -- and switches are TOGGLES, so it can walk itself into a
configuration it cannot leave. With 3 and 4 set, neither remaining switch's
readable tile is reachable, and it never tries "3 only", "4 only", or unsetting
one. There are just 16 configurations; enumerate them as toggle sequences with
the floor search as the reachability oracle, which is the part that already
works. Everything else in the chain -- getting to Mossdeep, entering the gym,
reaching a switch through the slides, pressing it, verifying by flag -- is done
and fast.

**What remains** is to join the two halves. `press_floor_switches` tests each
permutation with `target in nav.reachable(...)`, and nav cannot model a slide --
that is the whole problem -- so the test always says no. The floor SEARCH is the
only honest reachability oracle here. Drive the permutation loop with it (or add
an A-press move to the search so it flips switches itself; the signature already
makes a flipped switch a new node).

**Earlier notes, still true:**
0. **Give the search an A-press move** when the player faces a switch. That is
   the missing transition; everything else is already in place, including
   `press_floor_switches` for the permutation approach and a signature that
   already carries the four flags so a flipped switch is a genuinely new node.
1. Build the model from MEASUREMENT, not from the behaviour names. Load one
   savestate at the gym entrance, then for each reachable cell adjacent to a
   slide, step onto it and record where the engine puts you. My probe was too
   slow because it used `goto` per sample on a maze floor -- drive it with
   `emu` steps from a single root state and BFS outward instead.
2. Feed those measured transitions to the planner, or skip modelling entirely
   and let `solve_warp_maze`-style savestate search own the floor, keyed on
   (position, switch flags). The switches are the second half of the puzzle and
   `press_floor_switches` already exists for them.
3. Everything up to the gym door is fast and works: the sea crossing now takes
   34 seconds and the chain flags are all set.

## session port-23: the badge-7 chain is through, and four nav bugs paid for it

**606 unit green.** The run had been at 6/8 badges for eight sessions. All three
story beats are now done, each verified by the var or flag it sets:

    VAR_MT_PYRE_STATE                   = 1
    VAR_SLATEPORT_HARBOR_STATE          = 2
    FLAG_EVIL_TEAM_ESCAPED_IN_SUBMARINE = True

Checkpoint `saves/badge7-chain-done.state`. The loop's own objective now reads
"go to MossdeepCity_Gym for badge 7 (TateAndLiza)" and it is crossing Route 124
to get there.

### Why it had been impossible, in order of discovery

1. **The gate solver ran on every map in Hoenn.**
   `special RotatingGate_InitPuzzle` sets `gRotatingGate_PuzzleCount` and
   nothing clears it, so after one visit to Fortree's gym it reads 7 forever.
   Every failed walk escalated into a rotating-gate search:
   "4000 nodes explored without reaching (21,29) on Route122" -- half an hour
   and 4000 savestates, on a mountain path with no gates. Only two maps in the
   game have them, and the decomp now says which. This is also where the 1,288
   leaked scratch directories came from.

2. **Surfing suspended collision.** `Cell.passable` IS `collision == 0`, so the
   surf override only ever ran for cells the collision bits refuse -- and let
   the planner swim into rock. On Route 122 the route to Mt Pyre's door opened
   with D into (8,11), water with collision 1; the engine refused every time
   and `goto` logged "stalled 12x at (8,10)". Removing the override produced a
   path around the rock immediately.

3. **`take_warp` never synced surf and died on encounters.** It plans its own
   approach, so without `_surf_sync` every water approach was invisible; and it
   routed with a bare `goto`, which RAISES, so one Tentacool ended the caller:
   "TravelInterrupted: a battle started at Route122 (6,27)".

4. **A warp counted as taken if ANY map changed.** Called with Slateport's
   harbour door while the player had drifted onto Route 134, a step across an
   ordinary map connection reported success. It is held to the destination the
   map data names now.

### Two beats I had simply read wrong
- **Slateport.** Both harbour doors refuse entry: (40,7) is on the boundary and
  steps into Route 134, and (28,12)'s only approach is occupied by a stationary
  object. That object IS the beat -- `SlateportCity_EventScript_CaptStern`,
  un-hidden by Mt Pyre. Talking to him plays the interview, clears the harbour
  flags and ends `warp MAP_SLATEPORT_CITY_HARBOR, 255, 11, 14`. The game walks
  you in; the door was never the way. Beat 2 then took 25 seconds.
- **The hideout grunt.** The flag is set by the AFTER-BATTLE branch:
  `trainerbattle_single TRAINER_HIDEOUT_B2F_GRUNT_1`, defeat script `_15D8FD`.
  Talking returned True and changed nothing, twice. And (23,19) is walkable but
  NOT reachable -- B2F is components joined only by self-warps, 180 cells in
  reach from the stairs -- so it needed `reach_cell`'s maze escalation, which
  solved it in 4 states / 3 hops.

### The widget, third time
Reported as "it hasn't moved in over 5000 seconds", and `live/default.png` was
89 minutes stale: publishing was opt-in per script and neither `safari_probe`
nor `badge7_chain` contained the string "LiveFeed". Inverted -- any Driver
opening a save under `saves/` publishes, `live-run` aliased to `default`, temp
paths silent so the test lanes are unaffected. `play.py` now reuses that feed
rather than attaching a second observer.

### Standing
- **Dex 28 caught / 90 seen.** Still the long pole, and still gated: DIVE
  (badge 7), WATERFALL (badge 8) and the SUPER ROD are where the rest lives.
- Next: badge 7 at Mossdeep, then HM08 in Steven's house, then the rod.

## session port-22: three stalls, one cause each, all verified by movement

**595 unit green.** Everything here was found by being told the screen was
stuck and then reading the game rather than the code.

### 1. "give seabird a pokeball to hold" -- it really was
The wedged savestate, in the game's own words:

    msg: "SEA BIRD is already holding one GREAT BALL.
          Would you like to switch the two items?"
    displayed pocket: 1 (POKE BALLS)   selected: 3 (GREAT BALL)

`teach_pending_hms` runs every step, and `teach()` made two assumptions:
`_selected_item(pocket)` reads THAT pocket's scroll state (right data, wrong
thing, when the bag is displaying another pocket), and "its first row is USE for
a machine" -- true for a machine, and row 0 for a BALL is **GIVE**, which also
opens the party list. So it sailed through `_wait_for_party_list`, picked the
lead and handed it a ball. Now `_on_pocket()` reads `sCurrentBagPocket` before
any A press and `choose_use()` reads `sPopupMenuActionList` and navigates to the
real USE row, exactly as `fishing.Bag` always has.

### 2. The loop could not get out of a menu at all
Then it sat at Route 110 with a GREAT BALL's DESCRIPTION box up -- "A good BALL
with a higher catch rate than a POKe BALL." -- printing its status line every
cycle, 0 steps, while every movement press was silently refused. `step()` saw
`scene_active()` and called `advance_scene`, which knows a cutscene from a fade
and cannot dismiss a bag popup. `escape_menus()` walks out with B (the safe key:
blind A BUYS in a shop and CONFIRMS in a popup) and VERIFIES against
`scene_active()` rather than counting presses. Proven on that exact savestate.

### 3. A dead end I created, and it was not a wedge at all
Promoting the dex-23 Safari fork rolled the timeline back past the run's
Lilycove visit, clearing `FLAG_VISITED_LILYCOVE_CITY`. That one flag gates the
region map, so FLY refused its landing and BOTH objectives became unroutable:

    could not fly to LilycoveCity: not-visited
    could not reach MtPyre_Summit / SafariZone_Southwest: no walkable route

Seven minutes of a run talking to itself, with `scene_active` False and a
perfectly healthy emulator -- which is why the menu work never touched it.
`scripts/revisit_lilycove.py` flies to Fortree and walks east in single legs:
thirty seconds, and it verifies by LISTING the fly landings afterwards rather
than trusting the flag. **Next promotion: compare flags, not just the dex.**

### 4. The searches were eating the disk
`solve_gate_maze` and `solve_warp_maze` both `mkdtemp` and never removed it, on
any exit path. Counted: 38 `gatemaze-*` and **1,288** `warpmaze*` directories,
5.5 GB of tmpfs quota. It surfaced as a GAMEPLAY error --
"could not reach LilycoveCity_DepartmentStore_2F: [Errno 122] Disk quota
exceeded" -- three layers from the cause. A search bounded in nodes and seconds
was unbounded on disk. Both now rmtree their scratch; verified on the FAILING
path, which is where `return False` used to skip cleanup.

### Movement, as the actual test
Before: 1 distinct position in 45 s. After: **34 in 40 s**, `scene False`, /tmp
stable at 6.0 GB with 2 self-cleaning scratch dirs.

Dex 27 / 178. The three stalls are gone; the dex is still the long pole.

## session port-21: the ball-holding wedge, and why it looked like a loop

**595 unit green.** Two visible stalls reported from the couch, both real, and
neither one what I had assumed.

### "still trying to give seabird a pokeball to hold"
It was doing exactly that. The wedged savestate said so:

    msg: "SEA BIRD is already holding one GREAT BALL.
          Would you like to switch the two items?"
    displayed pocket: 1 (POKE BALLS)   selected item: 3 (GREAT BALL)
    scene_active: True

An unanswered YES/NO box, position frozen at Route 110 (6,38), 0 steps, for
fourteen minutes. SEA BIRD really was holding a GREAT BALL, so an earlier pass
had already completed one give.

Two assumptions in `teach()` combined, and `teach_pending_hms` runs every step:

1. `_selected_item(pocket)` reads THAT pocket's scroll state -- correct data
   about the wrong thing while the bag is DISPLAYING another pocket. The
   verification passed with the visible cursor on a GREAT BALL.
2. "A opens the item's popup; its first row is USE for a machine." For a
   machine it is. The bug was making the assumption at all: row 0 for a ball is
   GIVE, and **GIVE opens the party list too** -- so the flow sailed through
   `_wait_for_party_list`, picked the lead, and handed it the ball.

Both closed. `_on_pocket()` reads `sCurrentBagPocket` before any A press, and
`choose_use()` reads `sPopupMenuActionList` + `gUnknown_02038564` and navigates
to the real USE row, refusing when the popup offers none -- which is what
`fishing.Bag.choose_use` has done all along. The lesson generalises to gotchas
13 and 18: **any menu whose rows you do not read is a menu that will eventually
do something else.**

### "we're still stuck trying to give lottad a pokeball" (the first report)
Not a loop, but every catch looked like one. Per catch: a model call worth up to
19 s (or a 60-second open circuit), then THREE failed attempts to type a
nickname, each ending "could not move the cursor to 'Z' at (6,3)" and
re-offering the prompt. My earlier settle-on-input-state fix was necessary and
not sufficient -- the cursor genuinely cannot be driven on that screen.
Nicknames are off by default now; `accept()` takes the species name the game
already put in the buffer. A name has no bearing on the dex.

### Dex progress this session: 22 -> 27
- WOBBUFFET and NATU from the Safari (the first Safari catches ever made here).
- DODUO, and the loop caught more on its own once naming stopped costing a
  minute per catch.
- `dex_caught()` was answering about whatever stood on the field rather than the
  species it was asked about -- right by luck inside `plan()`, a trap anywhere
  else.

### A cost I paid and should name
Promoting the dex-23 Safari fork rolled the run back to before it had visited
Lilycove, so `FLAG_VISITED_LILYCOVE_CITY` went clear and FLY lost a landing:
"could not fly to LilycoveCity: not-visited", and Mt. Pyre became unroutable
from Slateport. +1 species, -1 fly target. A fork promotion is a timeline
rollback, and the flags it silently drops are not visible in a dex count. The
loop adapted on its own ("better hunting on SafariZone_Southwest; heading
there") and is now collecting there natively, which is the better arrangement
anyway: the live run reached 27 while the fork sat at 26, so nothing needed
promoting.

### Standing
- Live run: **27 caught / 90 seen**, 6/8 badges, running.
- From-zero run: still going, 15 caught.
- 27 of 178. Not a Pokedex.

## session port-20: the Safari Zone pays out -- WOBBUFFET, dex 23 (Aug 31 2026)

**585 unit green.** The first Safari catch this harness has ever made, and the
proof that all four doors between the zone and the Pokedex are now open.

    [catch] going for it -- WOBBUFFET is new to the Pokedex
    SafariZone_Southeast: caught 1 (dex 23)
    RESULT dex 22 -> 23 | new natdex [202] | balls 17 steps 438

WOBBUFFET is a Safari-only species. Checkpoint `saves/dex23-wobbuffet.state`.

### The four doors, in the order they were shut
1. **`battle_ready()` could never be true in the zone.** It demanded a non-zero
   species from EVERY battler, and a Safari battle memsets the PLAYER side of
   `gBattleMons` to zero on every controller pass, by design
   (`pret/src/battle_main.c:3711-3715`). Readiness was waiting for a mon the
   engine deliberately erases. Fixed earlier this session; now confirmed live:
   `battle_ready: True | kinds ('wild', 'safari')` with a fully populated foe
   (ODDISH L27, four moves) beside a `me` reading species `'-'`, level 0, no
   moves -- exactly what the decomp describes.
2. **The balls were counted from the wrong pool.** `balls_available()` read the
   ball POCKET; inside the zone the throwable thirty live in `gNumSafariBalls`
   in EWRAM (`pret/src/safari_zone.c:28,62`). The BALL_RESERVE guard was
   measuring Poke Balls the game will not let you throw. `GameState` now reads
   that and `gSafariZoneStepCounter` -- neither had ever been read here, and the
   step counter is the difference between retiring on purpose and being ejected
   mid-sweep.
3. **The throw waited for damage that cannot happen.** `decide()` held its ball
   until the target weakened. With no player mon there are no moves, so the
   target sits at full HP while a 15%-per-turn flee roll runs
   (`battle_ai_script_commands.c:1668-1674`). Throw on turn one instead.
4. **Nothing raised the odds.** A 24-minute sweep threw seven balls and caught
   nothing -- roughly `catch_rate/2`, every throw to four shakes
   (`battle_script_commands.c:9450-9494`). GO NEAR is the only lever, and the
   ROM's own tables say take exactly ONE: the catch bonus falls 4,3,2,1 while
   the flee penalty stays a flat 4 (`pret/data/btl_attrs.s:380-391`).
   `safari_go_near()` plus one approach per battle, and the next sweep landed
   WOBBUFFET.

### A refusal that was explaining itself into a void
The log printed "the catcher set no reason" against decisions that had written
a full sentence. `plan()` only sets `last_reason` on its early guards; the falsy
`CatchPlan` carries `.reason`. Now read from the plan first, which turned a
blank into "ODDISH adds nothing (scores -5.0: resists the team's open ELECTR;
but duplicates GRASS the team already fields; and owes 20 level(s) of
training)". That decline is correct -- ODDISH was caught earlier tonight.

### Where it stands
- Live run: **dex 23 caught / 89 seen**, 6/8 badges, promoted and running.
- From-zero run: nine hours unattended, exit 0, Rustboro, 15 caught, party of
  six. Restarted for another 600 minutes.
- `scripts/safari_probe.py` owns getting in (a `coord_event` at (8,4), not a
  person) and hands the sweep to `collect.py`.
- Still 23 of 178. The Safari holds ~12 more species behind nothing but time,
  and that is now a matter of running the sweep rather than fixing anything.

## session port-19: past the deadline -- item use, two tool steps, and the feed cleared (Aug 31 2026)

**578 unit green, 39 integration green.** Work after the 07:00 mark, since the
objective outlives the clock ("whenever we do manage to complete the pokedex,
whether that's tonight or some other day").

### The flicker: retracting my own "not confirmed"
Last entry said the cure was unconfirmed because the feed showed ONE distinct
PNG in 45 s. That was a bad reading, and the correction matters more than the
original claim: sampling the PNG hash AND the live position together gives

    5 distinct PNGs in 50 s, frames_published +47, position CONSTANT at (17,32)

The screen was not changing because the game was not moving -- the loop was
inside a bounded savestate search, which loads a state and re-renders the same
frame. `emu.screenshot()` was already proved innocent (5 distinct hashes over 6
ticks, changes on a step). So the publisher is healthy and the withholding
filter -- which WAS the pop-in -- is gone. Lesson: never read "the picture is
static" as "the publisher is broken" without reading the position alongside it.

### Two tools the chain never asked for
Both givers have NO gate in their scripts, and `PROLOGUE` did not mention
either, so the run walked past them for the entire game:

- **HM05 FLASH** -- a HIKER at `GraniteCave_1F` (36,9), handed over with no
  YES/NO (`scripts.inc:7-13`). The run passed him three times delivering
  Steven's letter. `VictoryRoad_B1F` and `_B2F` both declare `requires_flash`.
- **SUPER ROD** -- a FISHERMAN at `MossdeepCity_House3` (4,4), `MSGBOX_YESNO`
  (`scripts.inc:7-16`). The highest-value collectible left for a dex run: the
  rod decides which fishing slots roll at all and ~30 species live only in
  slots it reaches.

Wired and verified against the live save: the chain is 38 steps, FLASH
evaluates `offered=True` and became the pending objective immediately, and the
SUPER ROD correctly evaluates `offered=False` because badge 7 is not held.

### An out-of-battle bag, at last
There was no way to use a bag item outside battle -- `use_item` existed only
inside `pokeagent/battle.py`. So every evolution stone in the game was
unreachable, and all six sit on ONE Lilycove counter.

`Teacher.use_on_mon(item, mon)` reuses the machinery `teach()` proved against
the TM pocket, with the pocket-bound helpers generalised (`_reach_pocket` pages
LEFT as well as RIGHT -- ITEMS sits BEFORE TMs&HMs, so a right-only walk from a
bag opened on TMs&HMs ran out of tries). **Proved end to end on the live save:**

    use_on_mon("SUPER POTION", "SEA BIRD") -> True
    SEA BIRD 99 -> 107/107, pocket 10 -> 9

A potion rather than the stone it was written for, and the reason is recorded:
the stone counter is five floors up and `travel` cannot route a multi-floor
interior -- a probe spent fifteen minutes in a savestate search on the shop
door. `scripts/stone_probe.py` keeps the warp chain and the clerk cell so the
next attempt starts from coordinates.

Found on the way, and load-bearing: `_item_id` compared only the FIRST
underscore segment of each constant. Fine for `ITEM_TM23`, wrong for every
other item -- `ITEM_WATER_STONE` reduced to "WATER", so a WATER STONE was "not
an item this ROM knows about". All six stones now resolve (93-98) with TM/HM
lookups unchanged.

### The from-zero run finished its budget
Nine hours unattended, exit 0: **RustboroCity, 15 caught, 25 seen, party of 6,
0 badges.** It is the standing evidence that a fresh boot plays itself; it is
also honest about the pace, and the two blockers it hit all night (the Rusturf
Tunnel needs ROCK SMASH it has not earned, and it correctly said so) are real
game gates rather than harness faults. Restarted for another 600 minutes.

### Objective scoring, unchanged where it should be
- **Dex 22/178. NOT complete.** Everything built tonight serves it; none of it
  finished it.
- **Fresh-boot replay: not attempted**, still gated on dex completion.
- **Flicker: cause removed, and now confirmed healthy** -- 5 distinct frames in
  50 s while the game moved.

## session port-18: the flicker's real cause, the Safari door, and two ROM plans (Aug 31 2026)

**578 unit green, 39 integration green.** Deadline session. Three things the
objective named, and honest scoring on each.

### The flicker: the filter WAS the flicker
Reported again: "that game screen pop-in/flash/flickering thing is still
happening." It was, and the cause was my own earlier fix.

The publisher DETECTED fade frames and WITHHELD them, holding the widget on its
last real picture. The skip was bounded, so after MAX_TRANSITION_SKIPS in a row
a black frame went out anyway. Play that back: stale view, stale, stale, sudden
stab of black, back to the game. That IS a pop-in, on every warp, battle start,
heal and map load -- most of what a run does. The filter manufactured the
artifact it was written to remove, and it had already frozen the feed outright
once (ONE distinct frame in ninety seconds) when the counter reset sat in the
wrong branch. That near-miss should have been the tell.

A fade is not a lie about the game: it is what the console shows, it is
gradual, and consecutive samples during one render AS a fade. Every sampled
frame is published now; the detector only counts (`fades_seen`).

**Not yet confirmed cured on the live widget.** After the change the feed still
measured ONE distinct PNG in 45 s while `frames_published` climbed 136 -> 143
and the player walked (29,8) -> (37,8). What that rules OUT is worth recording:
`emu.screenshot()` is innocent -- driven directly it produced 5 distinct hashes
over 6 ticks and changed on a step. So the publisher writes, the emulator
renders, and the bytes on disk still repeat. Next session starts there, with
those two facts in hand.

### The Safari door, opened on a scout's correction
I had assumed the Safari blocker was `catching.py`'s `frame["wild"]` guard. It
is not: `wild` is `not (flags & BATTLE_TYPE_TRAINER)` and a Safari battle sets
ONLY `BATTLE_TYPE_SAFARI` (0x0080, assigned not OR'd,
`pret/src/battle_setup.c:547-548`), so that guard passes.

The real door was `state.battle_ready()`. It demanded a non-zero species from
EVERY battler, and a Safari battle memsets the PLAYER side of `gBattleMons` to
zero on every controller pass, by design
(`pret/src/battle_main.c:3711-3715`). Readiness was waiting for a mon the
engine deliberately erases, which is why every Safari encounter logged
"no battle frame (battle_ready never came true)" and the catch decision was
never consulted. Fixed and pinned: the live shape is ready, a Safari battle
before the foe arrives is still NOT ready, an ordinary wild battle still demands
both sides.

### Two ROM-grounded plans, so the next session does not re-derive them
- `DEX_PLAN_GATES.md` (401 lines): the ordered spine from 6/8 badges to
  Champion with the flag or var each beat tests and sets; Safari mechanics
  including the step/ball counters in EWRAM that NOTHING in this repo reads;
  the catch formula, from which the target is always at full HP and the first
  GO NEAR is the only clearly profitable one; and two ungated tools the run has
  walked past that `PROLOGUE` does not model at all -- the **SUPER ROD**
  (`MossdeepCity_House3` (4,4), no gate, ~30 species live only in fishing
  slots) and **HM05 FLASH** (`GraniteCave_1F` (36,9), blocks Victory Road).
- `DEX_PLAN_ENCOUNTERS.md` (705 lines): 186 achievable species with map,
  method, slot chance and gate; the exclusion list proved from the ROM rather
  than asserted; and the finding that all six evolution stones sit on ONE
  Lilycove counter -- eight evolutions for zero travel.

That second scout also found a live bug: `scripts/build_guide.py` read
`slot.method` and `slot.chance` through `getattr(..., None)` while `WildSlot`
declares `kind` and `slot_chance`. Every method and chance in
`docs/guide/encounters.json` was null and the file still looked well-formed. A
defensive default on a name you control turns a typo into missing data. Fixed
and regenerated: 0 of 1631 rows null.

### Also fixed tonight
`fly_to` now walks out the door before refusing indoors. The loop healed at a
Centre and tried to fly from inside it; the engine correctly refuses
(`MAP_TYPE_INDOOR`) and the router could not route out either, so the run sat
on the nurse's tile twice for half an hour each, ~2 fps. Every component was
right; nobody opened the door. Live: from inside FortreeCity_Mart,
`fly_to('SlateportCity')` -> True, landed (19,20); restarted onto it the run
left immediately, **6,464 frames in 75 s**.

### Scoring the objective honestly
- **Dex: 22 caught / 88 seen of 178 achievable in one save.** NOT complete. The
  machinery is proven in the small -- catching, fishing, the Safari, typed
  nicknames -- and tonight went to removing the reasons it was lying rather
  than to Pokemon.
- **Full-game replay from a fresh boot: not attempted.** It is gated on the dex
  being complete, and the from-zero run (15 caught, 0 badges, playing itself
  unattended all night) is the standing evidence that the boot path works.
- **Flicker: cause found and removed; cure not yet observed on the widget.**

## session port-17: closing state, and the two wedges that ate the night (Aug 31 2026)

**573 unit green.** Continuation of port-16. Catching now works end to end and
the numbers moved, but the honest headline is that most of the night's wall
clock went to two wedges rather than to Pokemon.

### It catches now, with names
After the balls, the settled frame and the grass fix landed, the play loop
caught **LINOONE, MARILL, ODDISH, ZIGZAG, MEDITITE and SHUPPET** in half an
hour, each one typed on the naming keyboard rather than taking a default --
which is the naming fix proving itself in the only way that counts. Party was
full, so they went to the box: the full-party redirect works.

**Dex 16 -> 22 caught, 79 -> 88 seen.** Checkpoint `saves/dex22-safari.state`.

### The Safari Zone, entered and exercised for the first time
Reached the gate from Fortree in 26 seconds by land (Lilycove is still
unvisited, so `fly_to("LilycoveCity")` correctly refused with `not-visited`),
paid the 500 (18568 -> 18068) and warped into `SafariZone_Southeast`. The
Safari battle controller I wired blind then ran for real and caught one. Two
things stopped it being more: the reason string on later refusals comes back
EMPTY (`declined None:` with nothing after the colon), and the zone's own
500-step limit ejected the run. The controller is no longer untested; it is
now known-partial, which is a better place to leave it.

### The two wedges
1. **A savestate saved mid-script is a landmine.** The restock left the shop
   after four B presses, which was one box too few -- the item DESCRIPTION was
   still up ("A good BALL with a higher catch rate than a POKé BALL.") -- and
   the save captured a script owning input. Every process that loaded it
   afterwards refused to move: `step_dir` returns False for FREE with
   "scene-owns-input", so the play loop sat at FortreeCity_Mart (1,5) for
   nineteen minutes logging "0 battles, 0 steps" while its stall recovery fired
   three times. Two B presses fixed it in a second, once the screen was read
   instead of the position. `save()` now refuses while a script owns input.
2. **Nobody opened the door.** The loop sat at LilycoveCity_PokemonCenter_1F
   (7,4) twice, half an hour each, advancing 3,885 frames in 28 minutes -- about
   two frames a second. Cause, quoted from its own log: it heals at the Centre
   and then, still on the nurse's tile, tries to FLY. `MAP_TYPE_INDOOR`, so
   `Overworld_MapTypeAllowsTeleportAndFly` refuses; the map router could not
   route out of the building either. The engine's rule was right, the refusal
   honest, the stall detector saw it -- and nothing walked outside.

   FIXED: `Flight.step_outside()` takes the warp that lands somewhere flyable
   and re-checks, up to three doors deep, and `fly_to` calls it before giving
   up. Live from inside FortreeCity_Mart with `flyable_here()` False,
   `fly_to('SlateportCity')` -> True, landed (19,20). Restarted onto it, the run
   left the Centre immediately: **6,464 frames in 75 seconds and (28,7) ->
   (66,12) across Lilycove**, against ~2 fps and stationary before.

   The lesson worth keeping is the diagnostic one: "frames not advancing" was
   the only signal, and the run's own status line does not carry it. Position,
   HP and battle count all looked fine while nothing happened.

### Where it ends
- Live run: 6/8 badges, **22 caught / 88 seen**, 23 balls, exploring Lilycove
  on the proven loop. `dex22-safari.state` is the promotable checkpoint.
- From-zero run: **15 caught**, 0 badges, unattended since last evening, still
  playing itself -- the "restart and watch it play" deliverable, honestly still
  working.
- The dex is 22 of ~180. The machinery to fill it exists and is proven in the
  small; the wall clock went to making it not lie, and I would rather hand over
  six real fixes and a truthful number than a bigger number I could not defend.

## session port-16: the collection driver, and five blockers behind it (Aug 31 2026)

**573 unit green.** The objective is the Hoenn Pokedex, and the run had been
stuck at 16-18 caught for hours. This session found out why. Every item below
was diagnosed by watching a live run and making the code state its reason --
not one of them was visible in the source.

### The chain that was stopping every catch
1. **The play loop cannot collect a dex, structurally.** Its objective is the
   next badge, so `fish_for_dex` -- reachable only from `grind_step` -- almost
   never ran. Hoisting it onto `step()` produced a visible loop (bag opening
   over and over, read from the couch as "just giving lotad a pokeball to
   hold") and was REVERTED with the reason written next to the temptation.
   Collection needs to own its travel and its budget: `scripts/collect.py`.
2. **The catch plan was built from an unsettled battle frame.** Every encounter
   of a 15-minute run logged "(policy declined)". Made to state its reason:
   "trainer battle", with the enemy name reading `None` -- because
   `battle_frame()` carries no `wild` flag until `state.battle_ready()` says
   the mon block is populated. `play.py` already waited; the new driver did
   not. Screens lie during transitions, and so do battle frames.
3. **Then it was balls.** With that fixed the refusal became honest: "only 3
   balls left (reserve 3)". The catcher was working perfectly and there was
   nothing to throw.
4. **And the shop did not stock them.** The restock asked for "POKé BALL" and
   Fortree's mart answered "POKé BALL is not sold here" -- it is badge-6 tier,
   stocking GREAT BALL (600) and ULTRA BALL (1200) and no basic ball at all.
   The run had 41,968 in the bank. It now reads the shop's own list and buys
   the cheapest ball actually on the shelf: **bought 27x GREAT BALL for 16200
   (bag 1 -> 28, money 41968 -> 25768)**. Checkpoint `badge6-stocked.state`.
5. **Pacing happened wherever the map was ENTERED.** Route 118 is entered at
   (10,13); its grass runs x=51..55. A probe walked 219 laps on the path
   without one encounter. Now it asks `find_tiles(map,'grass')`, intersects
   reachability, and walks there.

### Two crashes and a deadlock, all on recovery paths
- **`self.wild()` has never existed**, and it sat on the only path that escapes
  a stalled battle: four flat turns raised `'BattleSession' object has no
  attribute 'wild'` and killed the process. `can_flee()` (the port of
  `CanRunFromBattle`) is the real predicate. A test now asserts `play()` calls
  no such name -- a crash on the recovery path is worse than the thing it
  recovers from.
- **The active battler was proposed as its own replacement.** Route 121, a
  KECLEON on 13 HP, MIGHTYENA active in slot 1 with 0 PP on all four moves,
  four healthy mons benched. The fallback's test was `if idx`, excluding only
  slot 0, so it offered ("switch", 1) forever: "party slot 1 is already the
  active battler" -> retired -> flee refused (scripted ambush counts as a
  trainer battle) -> nothing left. **Twelve minutes** of spin on a winnable
  fight. Now it skips the known active index, keeping the slot-0 proxy only
  when the analysis has no index to give.
- **7.5 MILLION no-op steps in 150 seconds.** `step_dir` returns False for free
  while a scene owns input ("scene-owns-input (gPlayerAvatar.preventStep)"), so
  the pacer asked, was refused, and asked again. It walks with `goto` now.

### Also fixed
- **The widget went dark for 43 minutes** and the report was exactly right:
  "its been dead for a while?". Only `play.py` attached a `LiveFeed`, so
  swapping in the collector froze `live/default.png` while the emulator kept
  working. Anything that drives the game for hours owns the feed.
- **The naming keyboard could not reach a single letter.** Every catch took a
  default name ("could not move the cursor to 'G' at (0,1)"). Input is only
  read in `sMainStateFuncs` index 2; the settle was a flat 30 ticks measured on
  the player-name keyboard, and the catch keyboard arrives out of a battle with
  its fade still running. It waits on the engine's state now, and costs zero
  frames when already enabled.
- **A budget that only applies BETWEEN attempts is not a timeout.** The nurse
  trip was wrapped in `while time.time() < deadline`, and one
  `heal_at_nearest_center` call sat inside it for 29 minutes with the log
  frozen. Capped at two attempts, and it says so and carries on hurt.

### Where it stands
- Live run: badge 6, 18 caught, 30 balls, party healthy-ish, on the proven
  `play.py` loop for the remaining hours -- chosen over the newer collector
  precisely because the collector still needs a per-call watchdog to be
  trusted unattended.
- From-zero run: still playing itself, 12 caught, working toward badge 1.
- `collect.py` is real and proved itself (dex 16 -> 18 in ten minutes,
  unattended, flying and fishing and saving after every catch). It is not yet
  trustworthy for five hours alone, and the journal says so rather than the
  commit message implying otherwise.

## session port-15: FLY, the tools, and an upstream PR (Aug 31 2026)

**567 unit green.** Badge 6 was won last session; this one spent its time on
the things that were blocking the Pokedex, and on one honest revert.

### FLY, and what it unlocked in seconds
The move-forget screen was the last thing standing between the party and FLY,
and it failed for two reasons, both measured rather than guessed:

- The prompt "<MON> wants to learn ... However ..." does not advance on a short
  tap. Eight presses each: **A:2 no, A:6 no, A:10 no, A:16 yes.** The old `A:2`
  comment was right about the level-up box and wrong about this one.
- The slot was chosen by COUNTING presses, which this project forbids, and
  which had already cost a BLAZIKEN its BLAZE KICK. The move list has a
  readable selection (`pssData.selectedMoveIndex`) and it tracks exactly --
  0,0,1,2,3,4 over five DOWN presses, the first swallowed while the list draws.

`forced_loss` was also pricing a sacrifice that would never happen: it answered
`moves[0]` on the theory that the engine always takes slot 0, so teaching FLY
was refused as "would overwrite SURF (95 power)" when the real casualty was
SWALLOW, power 0, useless without Stockpile.

Then `Driver.fly_to` landed (delegated, ROM-derived landing table, region-map
cursor read rather than counted), and the three collectibles the run had spent
hours failing to WALK to arrived in about twenty seconds of emulation:

    fly MauvilleCity -> Route118 -> talk (28,8)      GOOD ROD
    fly DewfordTown  -> talk (12,14)                 OLD ROD
    fly SlateportCity -> contest lobby -> talk (1,5)  POKEBLOCK CASE

Fishing then produced a species immediately: **WAILMER, dex 15 -> 16.**

### The PP complaint, fourth guard
Reported again, and the previous three guards were all in places nothing
called. `attack()` now SUBSTITUTES: asked for an empty slot it plays the
strongest move that actually has PP and logs who asked for what with the whole
moveset and its PP attached. Refusing was correct and still wasted the turn,
which from outside is indistinguishable from trying the dead move.

Also fixed: `heal()` only asked about HP, so a party at full health with nothing
to attack with was told there was nothing to heal -- forever, inside a Centre.
The nurse restores both.

### One revert, on purpose
`fish_for_dex` only ran from `grind_step`, so with the objective almost always
"the next badge" the run walked past every bank in Hoenn with two rods. Calling
it from `step()` fixed that and created something worse: it walks to the bank
itself, so the walk plus the bag flow ran every cycle and read on screen as the
run repeatedly offering a Poke Ball to LOTTAD. One call sat in a goto for the
whole 280-second probe. Reverted, with the reason written next to the
temptation. Held items were verified clean afterwards.

### Upstream PR
Branch `multigen/paths-and-lazy-constants` pushed to
github.com/<upstream>/crystal-omp-agent (commit 4b70b6c), two surgical fixes plus
`MULTIGEN_NOTES.md`:

- `paths.py`: `REPO_ROOT` overridable via `CRYSTAL_REPO`, identical default.
- `state.py`: battle constants parsed at first use, not at import.

Upstream's own suite could not even be COLLECTED from a bare checkout --
`Interrupted: 43 errors during collection` -- and with the two changes against a
plain pret/pokecrystal clone it is **653 passed, 31 skipped, 7 errors**, the
errors being a `pokecrystal.sym` that needs a built ROM. An earlier version of
the patch removed `_STATUS_BITS` and broke their own
`test_parser_values.py`; it is preserved through PEP 562 `__getattr__` instead.
Nothing of ours was touched: our tree stayed clean at c6da79a and both live runs
kept running throughout.

### Honest state at the deadline
- **Dex 16 caught / 180 achievable.** Not close. The prerequisite research put
  only ~30 species within reach of this save without the Dive/Waterfall chain,
  and the machinery to collect them (catching at all, fishing, Safari battles,
  FLY) only came unblocked in the last several hours.
- 6/8 badges. `saves/badge6-tools.state` is the milestone: badge 6, FLY, both
  rods, the Pokeblock Case.
- **A from-zero run has played itself all night** -- truck, clock, rival,
  starter, Petalburg, Rustboro -- and is at 10 caught with 0 badges, which is
  the "restart and watch it play" deliverable working end to end.
- **The widget flicker is fixed and measured**: 0 degenerate frames over 120
  seconds against 9 (including pure black and near-white) before.

### Still open
- The Safari Zone is enterable now (Case + money) but nothing drives it; the
  battle controller is implemented and has never been live-tested.
- The naming keyboard cannot reach every letter ("could not move the cursor to
  'Z'"), so catches take default names.
- Fishing needs a bounded collection phase that owns its travel.

## session port-14: BADGE 6, and why a day was spent at five (Aug 30-31 2026)

**523 unit green.** The run sat at 5/8 badges for about twenty-four hours. It
was not one bug; it was a queue of them, each hidden behind the last, and every
one of them was a wall the harness invented rather than one the game imposed.

### The chain, in the order it was found
1. **Route 119 north** -- nav treated a MUDDY SLOPE as ordinary floor, so
   `find_path` took the two-tile shortcut, the engine slid the player back
   south (`ForcedMovement_MuddySlope`), and goto replanned the identical path
   forty times. Slopes are now enterable downhill only; the climb needs the
   MACH bike at full speed and the ACRO bike genuinely cannot do it
   (`GetPlayerSpeed` returns 3 and the test is `> 3`).
2. **Mounting SURF off a cliff** -- with the slope refused the planner found
   the next shortcut: north into the river from an elevation-4 plateau three
   levels above the water. "Could not mount SURF facing U", forty times. The
   first fix for this lived inside the elevation branch, which exempts the
   wildcard levels -- and Route 119's channel is elevation 0, so it came
   straight back one cell west. The guard stands alone now.
3. **A replan cap that charged progress** -- chunks are six steps, so a
   166-step route needs twenty-eight replans before a single wild encounter
   adds more. Eighty rows of real walking reported "replan-cap reached".
4. **A stale reachability cache in `travel`** -- `goto` marks live NPC bodies
   every pass and `mark_blocked` is what invalidates the cache; travel marked
   only gates. Caught red-handed on a fresh save at Littleroot's north seam:
   every gate variable satisfied, the connection right there in `exits()`, and
   `route_legs` answering None. Marking NPCs -- which only clears the cache --
   made the identical call return a plan.
5. **FORTREE'S GYM IS SEVEN ROTATING GATES** and they are invisible to every
   model: not metatiles, not object events, so `grid_drift` reads zero and nav
   reported 205 reachable cells with both Winona and the exit among them. The
   first step was refused. The run could not win the badge and could not leave
   the room. Orientations ARE readable -- one byte per gate at
   `GetVarPointer(0x4000)` -- so a node is (position, gate orientations) and
   the moves are the four steps, because walking into a gate is how you turn
   it. Best-first, because plain BFS spent 500 nodes in the south while Winona
   stood at the top.
6. **Gen 3 only loads object events near the camera.** Winona is at (4,1) in
   the map data and simply ABSENT from `gObjectEvents` while the player stands
   at (16,20) -- so the first `talk_to` was aimed at an empty tile and returned
   True having talked to nobody.
7. **A refused switch wedges the whole battle.** `switch_to` failing leaves the
   engine in `sub_802DF88`, the party-menu return handler, waiting on a
   condition a half-driven party screen never satisfies. The action menu never
   comes back and the battle ends "stuck". The same fight, attack-only, was won
   in 28 turns. **BADGE 6.**

### What else came out of the night
- **Catching was impossible.** `plan()` refused every encounter with "party is
  full", and the party has been six mons since Petalburg. `GiveMonToPlayer`
  boxes a catch when the party is full (pokemon_2.c:964-983); live storage had
  420 free slots. The dex now outranks team merit: a species whose CAUGHT flag
  is unset is worth a ball on sight.
- **And the decision was never consulted anyway** -- almost every wild battle
  interrupts a journey, and `goto`/`travel` call `Driver.fight()` with no
  policy. A standing `battle_policy` fixes that; verified by catching a
  ZIGZAGOON through the travel path.
- **The PROTECT loop** was a blind A press on the move picker in the battle
  loop -- the picker is a SELECTION, not dialog, and A there sends whatever the
  cursor sits on. Slot 0. PROTECT. Third instance of that pattern in this
  project.
- **FISHING exists now** (`pokeagent/fishing.py`): bag driver plus the reel,
  polling the task's own `tStep` and pressing on state 7. With the press
  removed as a control, 5 of 12 casts got away; with it, 0 of 20+.
- **The widget flicker was real and measured**: 366 published frames in 60s,
  nine of them degenerate. Fades are now withheld -- and my first version of
  that froze the feed to one frame in ninety seconds, which the re-measurement
  caught.
- **A fresh game plays itself.** The loop crashed on an empty party, nothing
  ran the intro, and the truck exit was on the wrong side. `drive_intro` now
  takes a new save from the truck to a starter unattended; a from-zero run has
  been playing all night and is past Petalburg.
- **The nurse restores PP**, and `heal()` only asked about HP -- so a party at
  full health with nothing to attack with was told there was nothing to heal,
  forever, inside a Centre.

### Honest state
6/8 badges. Dex **15 caught / 180 achievable**. The dex will not be finished
tonight: the prerequisite research puts only ~30 species within reach of this
save without the Dive/Waterfall chain, and the machinery to collect them was
only unblocked in the last few hours. `saves/badge6-winona.state` is the
milestone; `saves/live-run.state` continues from it.

Two known defects left open and NOT hidden: the move-FORGET screen cannot be
driven (so FLY is decided correctly and then not taught), and the rod/Pokeblock
steps are out of the story spine because as gates they only ever blocked.

## session port-13: the PROTECT loop, and the slope that never mattered (Aug 30 2026)

**460 unit + 33 integration green.** Three user reports, all three ground
truth, and two of them my earlier fixes aiming at the wrong target.

### "It keeps trying PROTECT with no PP" -- the third report was the right one
The guards I added first (a PP ledger, a usable-slot filter) both sat in
`attack()`, and `attack()` was never reached. The actual site was one line in
`battle.play()`:

```python
if not self.at_action_menu():
    self.emu.run_sequence("A:2 .:10")     # page through battle text
```

The move picker is not the action menu, so whenever it was open this pressed A
on whatever the cursor sat on -- slot 0, PROTECT, 0 PP -- every turn. Now the
picker gets a **B**, because it is a selection and not text. Verified in the
exact state: picker open on slot 0 at 0/10, loop backed out, tactics chose
HEADBUTT, KO, PROTECT untouched.

**Third instance of one pattern**: the mart's re-arming YES/NO box, Crystal's
Bill's PC (five of six mons deposited), and this. *A blind A loop over a menu
that re-arms itself is a repeat-action loop.* The ledger and cross-battle
futility memory are still worth having and are still in -- they stop a CHOSEN
empty move and stop re-learning that PROTECT does nothing, once per battle,
ten battles running.

### "It can't get up the mudslide -- don't you need the bike?"
Half right, and the half that is wrong matters. `ForcedMovement_MuddySlope`
(field_player_avatar.c:494-506) climbs only when
`movementDirection == DIR_NORTH && GetPlayerSpeed() > 3`. Exactly one state in
the game qualifies: the **MACH** bike at full acceleration (SPEED_FASTEST = 4).
The **ACRO bike returns 3, and 3 > 3 is false** -- it slides back like walking.
Anyone "fixing" this by buying a bike has a 50% chance of buying the wrong one.

But no bike was needed. Decoding the layout: Route 119 has exactly TWO muddy
slope cells, (6,54) and (6,55), and the reachable set is 1847 cells with them
and 1845 without -- the delta IS the two tiles. It is a shortcut. The Weather
Institute is reachable on foot, and three real bugs were hiding behind it:

1. nav treated the slope as ordinary floor, so `find_path` took the shortcut,
   the engine slid the player south, goto replanned the same path -- forty
   times. A slope is now enterable only going DOWN.
2. With that refused the planner found the next shortcut: SURF across the
   river **off a cliff**, elevation 4 down to 1. "Could not mount SURF facing
   U" x40. Mounting now requires a shore; dismounting stays free. The first
   attempt at this put the check inside the elevation branch, which exempts
   the wildcard levels -- and Route 119's channel is elevation 0, so the cliff
   crossing came straight back and the stall moved one cell west.
3. `goto`'s replan cap charged PROGRESS. Chunks are six steps, so a 166-step
   route needs 28 replans before a single encounter adds more; eighty rows of
   real progress reported "replan-cap reached". It counts STALLS now.

**Verified**: goto(6,33) True in 171s, take_warp(6,32) True ->
Route119_WeatherInstitute_1F (9,12). On foot.

### The zombie lead, root-caused
Why was one mon's PP always the PP that ran out? Because the run could not
change its lead. `partyorder` believed the party cursor unreadable and searched
press counts in a four-wide window; with six mons it never reached the last
slot, so MIGHTYENA never led and the loop blacklisted it. The cursor is
readable -- `gTasks[task].data[3] >> 8`, then that sprite's `data[0]`
(party_menu.c:1773-1776). Two further traps, both measured: the SWITCH popup
row is not a constant (five rows, SWITCH at 2, and `gMenu.cursorPos` is stale
when the popup opens, so the row is now confirmed by the engine's own task),
and in switch mode `slotId` is the PINNED pick while **`slotId2`** is the
cursor that moves. Rotation now works for the last of six.

### Also closed this stretch
- Planning is time-bounded (`nav.plan_budget_s`, 20s) in `route_legs` itself
  rather than at six call sites; a search that times out says so.
- `dive_gates` and `_landing_room` cached; borders priced on a SAMPLE.
- The component-alias optimisation was written, measured against the uncached
  BFS, found to over-report Route 110 as 1730 cells against a true 797, and
  REMOVED. Reachability is not symmetric here (bridges, ledges). The randomised
  check is why that did not ship.
- Gates: `closed_gates` now means "fires AND refuses passage". Route 119's
  rival ambush is a `trainerbattle`, not a wall, and marking it impassable
  severed the map at a two-cell corridor.

### Where it stands
Route 119, 5/8 badges, climbing north toward the Weather Institute under its
own steam. Party PP-dry: none. `saves/live-run.state`.

## session port-12 -- the five-minute budget, the stolen save target, DIVE/WATERFALL (Aug 30 2026)

**444 unit + 33 integration green.** Three of tonight's biggest problems were
not game problems at all.

**`--minutes` defaults to 5.** Every restart ran the play loop for five
minutes and exited; with the hub's default `restart: no` the run then sat DEAD
until the next manual restart. That, not pathfinding, explains most of the
night's pace. Now started with `--minutes 1440` and `restart: always`.

**A search stole the save target.** `Driver.load()` repoints `state_path`, and
`solve_warp_maze` loads dozens of scratch forks -- so from the moment the maze
solver first ran (Lavaridge, three badges) every periodic `d.save()` went to
/tmp. The run went on to win badges 4 and 5, take SURF and cross Route 118
while the working file sat frozen at three badges; a later restart replayed
from there. Nothing was lost -- the autosave RING writes explicit paths, so
live-auto5 held badges=5/surf/Route 118 and is restored (pinned as
live-badge5-surf.state). `load(adopt=False)` now exists so no future search
can make the same mistake, and the solver restores the pointer on every exit.
Lesson: "the run is advancing" is not evidence that the run is being SAVED.

**Route 119's northern half was severed by its own bridge.** Elevation was
missing from the BFS closed set, so an elevation-15 bridge cell -- which
accepts any level and preserves it -- was closed by whichever wave reached it
first: the z=3 wave down the river shut (21..23,84..85) against the z=4 road
and cut the map at y=82. Plus my own surf patch pinned surfers to their old
elevation, costing the elevation-0 river its wildcard. Reach 1234 -> 2619,
y 82..139 -> 3..139, and the institute is three legs away instead of a
twenty-leg sea circumnavigation via Slateport. Found by a scout reading the
engine against the harness's measurements; it also established there is no
third elevation mechanism in src/, that MB_WARP_OR_BRIDGE is never a warp,
and that MB_*_RAIL are impassable on foot (still unmodelled -- it only makes
the engine stricter).

**DIVE and WATERFALL implemented.** nav was discarding dive connections as
"not a walkable seam"; a dive is a VERTICAL seam that lands at the same (x,y)
(SetDiveWarp, overworld.c:583-600), with gates from the engine's own
predicates (diveable 0x11/0x12/0x14; surfacable = not 0x19/0x2A). Route 126
offers 1349 gates into Underwater2. Waterfall is a wall in every direction but
north and only past badge 8 (GetInteractedWaterScript needs badge 8 + surfing
+ facing north); walk() intercepts the northward step and climbs. Both refuse
before pressing and close menus on failure.

**scripts/atlas_audit.py** answers the "map everything up front" question
properly: the whole region already decodes in about a second, so precomputing
buys no speed -- but asking, for all 394 maps, whether each map's own event
cells are reachable from any legitimate arrival is the generalisation of every
wall this project has hit. 26 unreachable of 2984 (0.9%), essentially all
legitimately gated. Its value is the diff across a movement change.

Also: `_live_alternative` read Mon objects as dicts and killed the loop
mid-battle on Route 118 (the retirement fallback only runs after something
already went wrong -- the worst place for an assumption); the story path now
honours the travel give-up, which had retried one unreachable map 8125 times.

Run state: Route 119 (14,59) climbing north through the unsevered half,
5/8 badges, SURF, 24h budget, auto-restart on.

## session port-11 -- badges 4 AND 5 live; SURF; the gym trilogy (Aug 30 2026)

**439 green. Two badges fell tonight, both fully autonomous on the live run.**

Badge 4 (Flannery): the hot-spring maze beat four static models in a row --
trainer body ON a spring, goto's no-path probe erasing its own body marks,
per-hop replans diverging under warp drift, landing pads posing as springs
(B1F's up-springs are behavior 0x29; nothing in the header names them). The
answer that held is `solve_warp_maze`: fork savestates, step on every
reachable in-maze spring, BFS over OBSERVED outcomes, replay the winning hops
on the real timeline. 15 states, 10 hops, Flannery beaten -- and reach_cell
is now plan-first/solve-on-failure everywhere.

Badge 5 (Norman): the third kind of gym. Not a switch puzzle, not a warp
maze -- horizontal slices joined by A-PRESS script doors (bg_events on the
warp cells), each slice's ONWARD doors keyed on its trainer's defeat.
_enter_warp gained the third mechanism (face, A, script slides, step
through); play gained fight_the_room. The run climbed the whole gym alone,
took the badge, walked to Wally's house and collected HM03.

SURF: nav has a `surfing` mode (water passable, shoreline elevation exempt
both ways -- the dismount half stranded the first probe on a 46-cell pond),
walk() turns land->water steps into the face-water/A/YES mount, and the
loop had already taught SURF to SEA BIRD by itself. Verified: Route 118
crossed, Steven scene fired. Eastern Hoenn is open.

Also tonight: the cable car became scripted TRANSPORT (ride offered until
badge 4, plus a head_for fallback that rides whenever a mountain-side map is
unroutable); the ride step's condition is the town's visited flag because
the ash var resets off-mountain and orbited the run through a nine-leg loop
it executed flawlessly six times; a battle during a warp attempt is a battle,
not a broken warp; and the shell widget survived my own `omarchy refresh
shell` resetting the user's bar (restored from the backup; the flicker fixes
turned out to have never been loaded -- the running shell predated them by a
day).

Live run at last check: Route 116, 5/8 badges, SURF usable. Next: Route 118
east, Weather Institute, HM02 FLY, Devon Scope, Winona.

## session port-10 -- the elevation unlock; badge-4 chain encoded end to end (Aug 30 2026)

**439 unit + 37 integration-deselected green.** The night the map opened.

The find of the session: **elevation 0 is a transition, not a bridge.**
`ObjectEventUpdateZCoord` (src/event_object_movement.c:7586-7598) takes the
tile's elevation unless it is 0xF; `IsZCoordMismatchAt` passes z==0 onto any
level. nav treated 0 like 15 (keep your level), so no route could ever leave
the elevation it started on -- Route 114 read as 567/3200 walkable, Meteor
Falls' door sat in a "52-cell pocket", grid_drift() said ZERO drift (the model
was faithful to the .blk and wrong about the game), and issue #42 concluded
SURF was required. It was not. One rule fixed every level change in Hoenn.

Around it, the routing stack lost five stacked bugs (connection_landing had no
bounds check so Route 111's west edge landed on Route 113 at y=66 of a 20-row
map; route_legs returned terminal legs that landed nowhere and rebound its own
expansion cell; _cross_seam ignored the plan's cross_at and re-ranked locally;
nav.blocked never invalidated _reach_cache), plus: weather coord_events are
not gates (Route 113's ash blocked Fallarbor), a frozen player is not a wall
(preventStep vs goto's cell-poisoning), ledge jumps desynced walk(), the
destination cell must never be marked blocked, heal_at_nearest_center ranked
by graph hops not walkable routes (fainted Lottad in Fiery Path -> Lavaridge
forever), and take_warp learned walk-through edges (the Jagged Pass descent).

Battle: an action that changes neither HP twice is RETIRED for the battle;
an engine REFUSAL retires immediately (the 0-PP PROTECT loop); a stalled wild
battle is fled, not re-entered. play.py's stall key no longer contains the
battle counter -- a self-advancing value that kept the detector blind through
hundreds of dead turns. New pokeagent/watchdog.py carries the progress
signature + escalation ladder.

**The whole badge-3 -> Kyogre chain is encoded in quest.py** (27 steps from
the decomp with citations, via the RouteChain scout): Meteor Falls on foot
(Fallarbor side), cable car, Mt. Chimney leader, Jagged Pass, Flannery,
Go-Goggles, Norman, SURF, Route 118, Weather Institute, FLY, Devon Scope,
Kecleon, Winona, Mt. Pyre, Slateport harbor, hideout, Tate&Liza, DIVE,
Seafloor Cavern, WATERFALL, Kyogre. StoryStep grew `require` gates
("FLAG_X" / "!FLAG_X" / "VAR_X>=n") because the chain is flat and the game is
not. reach_cell() routes to a CELL through warps (Lavaridge's hole-maze gym).

**Dex target reconciled against the decomp** (DexPlan scout): 202 - 7 Ruby
exclusives - 6 trade evolutions - 3 event-only (Jirachi/Deoxys/Latios;
birch_pc.c:94-102, species.h:1283) = 186; Milotic back IN (EVO_BEAUTY on this
cartridge -- the ROM overrides the dataset's Gen-5 trade claim); unchosen
starter/fossil lines leave plan AND denominator via choice_locked() = 178
after both choices, matching the research count exactly.

Widget: the flash was the narration line mounting/unmounting at game speed
directly under the framebuffer (reserved two lines, faded not unmounted) plus
feed state nulling on inotify rename races (holds last good snapshot).

Where the run is: Mt. Chimney summit, fighting the grunt gauntlet toward the
leader at (13,6). Badges 3/8, dex 13/180 live target.

# PROGRESS

Newest session first. Every session appends; nothing is rewritten.

## session port-09 -- teaching HMs, and the rocks that were misdiagnosed twice (Aug 29 2026)

**404 unit + 37 integration green.** The run holds 3/8 badges, knows ROCK
SMASH, has smashed its way out of Route 111 and is on Route 112 heading for the
Mt. Chimney cable car.

**Two HMs had been sitting in the bag unusable** -- CUT since Rustboro, ROCK
SMASH since Mauville -- because the Gen-3 port could not teach a machine at
all. `field_moves()` reported all-None for three badges. That is Crystal's
gotcha 16 repeating in a new generation, and this time it cost a badge: the
road north out of Mauville is held by two breakable rocks.

`pokeagent/teaching.py` drives START -> BAG -> TMs&HMs -> machine -> USE ->
party member, refusing before pressing anything when the machine is unknown,
absent, unlearnable or already known. Four things the emulator corrected me on,
each after a confident wrong answer:

- **`"R"`/`"L"` in the input DSL are the SHOULDER buttons.** The d-pad is
  `LEFT`/`RIGHT`. Pocket switching "did not work" for an hour because of it.
- **`struct PocketScrollState` is FOUR bytes**, not two. Read as two, the
  cursor arithmetic ran on POKE BALLS while the code believed it was on
  TMs&HMs and reported a Great Ball as the selected machine.
- **`sCurrentBagPocket` is the engine's own answer** for which pocket shows.
  My clever alternative -- nudge the cursor, see which scroll state moves --
  mutates the thing it measures and named the wrong pocket anyway.
- **The bag SORTS its pocket**, so the cursor index is a position among
  present items, not a raw slot number.

The party cursor still cannot be read: `gLastFieldPokeMenuOpened` is written on
CONFIRM, not during navigation, so trusting it taught ROCK SMASH to MIGHTYENA
while reporting ROCKY. Selection is a counted press and the LEARNER is read
back from the moveset; when it is not who was asked for, that is said out loud.

**Ordering bug worth remembering**: the moveset is written BEFORE "X learned
ROCK SMASH!" finishes drawing, so backing out immediately fired B presses into
a message that swallowed them. The teach worked and the run still could not
walk afterwards.

**The rocks.** `S_BreakableRock` at Route 111 (18,101) and (19,100) had been
misdiagnosed twice already -- as a pathfinder bug, then as wandering trainers
boxing the player in. `goto`'s no-path branch now separates three cases: no
route at all, someone standing in it who will move, and something standing in
it that never will. The first gets a refusal, the second a settle, the third a
ROCK SMASH.

`clear_the_way`'s first version asked whether a path existed using the STATIC
grid before smashing. It always says yes -- the rock is an object, not terrain
-- so it declined to smash anything and reported success. It has to ask with
live objects marked, the same view `goto` uses.

**Still no offline walkthrough.** The story chain is 16 steps and ends at the
cable car; everything past badge 4 is still discovered by walking into it. The
decompilation is right there in `pret/` and extracting the whole gate chain in
one pass would be far cheaper than the 30-60 minutes each reactive discovery
has cost. That is the next structural improvement, not more gates one at a
time.

## session port-08 -- BADGE 3, and the map the game is actually walking on (Aug 29 2026)

**395 unit + 37 integration green. Wattson beaten -- 3/8 badges.** The run is
on Route 111 heading north, team 30-42.

Badge 3 needed five separate fixes, and only one of them was in the gym.

**Three doorways held by people, not walls.** Slateport's Oceanic Museum has
its two door tiles walled on three sides, and three Team Aqua grunts stood on
the only approaches -- 1171 failed attempts at `no approach to warp (30,26)`
before anyone rendered the entrance. Talking to Dock in Stern's Shipyard sets
`FLAG_HIDE_EVIL_TEAM_SLATEPORT` and they leave. Then Wally and his uncle hold
the entire approach to the Mauville gym door until `TRAINER_WALLY_2` is beaten.
Both are now story steps. **When `take_warp` says no approach, render the door
with live NPCs marked before touching the pathfinder** -- three times in a row
the geometry was right, nav was right, and the answer was a person.

**A one-cell pocket at a map seam.** Crossing from Verdanturf, the player
landed on Route 117 (0,7) and could not move in ANY direction -- raw d-pad
included, `preventStep` 0, no scene, no dialog. A border is forty doors and
they land in different places: (19,7) lands in a 1-cell pocket, (19,8) lands on
698 cells of road. `_cross_seam` sorted candidates by distance from the player,
so standing near y=7 it picked the trap. Everything now ranks by how much
walkable map a crossing LANDS on; distance only breaks ties.

**Training that could not terminate.** Laggards were measured against the party
MAXIMUM, so one over-levelled starter marked all five others as laggards
forever -- and training them raised the max too. Forty minutes grinding level-3
wilds on Route 102 with a level-26 party and a gym it could already beat.
Median now. Separately, ROTATION was gated behind that same list, so once it
was empty nothing rotated and NINJA ran L29 -> L42 while the rest sat at 27.
They are different questions and are now asked separately.

**The map that changed.** Mauville's gym is barriers worked by three floor
switches, and `.blk` files describe the map as SHIPPED -- so nav saw Wattson in
a component it could not enter and said so, correctly. `live_grid()` /
`grid_drift()` / `sync_grid()` read `gBackupMapLayout` (7-tile border, masks
from global.fieldmap.h:7-9) and hand the difference to nav. Switch (4,15) moves
17 cells and Wattson becomes reachable.

Pressing switches in order does not work -- they are TOGGLES, and the first one
destroys the state the second needed. So the search forks: save, try sequences
in increasing length, reload between trials, keep the first that works. Same
state plus same inputs is byte-identical, so a failed trial costs only frames.

**And the fix that made all of it count.** `Driver.load` repoints
`state_path`, so from the first search onward every autosave went to a temp
directory. The puzzle was solved -- "barrier puzzle solved: (0, 9)" is in the
log -- and thrown away, fifteen minutes at a time, while the log insisted the
run was advancing. Nothing errored. Nothing warned.

**Metrics are live**: 2 badges timed, so the feed now publishes
~48.5 h real elapsed to eight badges, with "2 badges, 1 usable gap" attached.
The in-game figure (696 h) is honest but distorted -- the cartridge clock ran
through every wedge above.

## session port-07 -- off the Dewford island; four ways a working action looked broken (Aug 29 2026)

**378 unit + 37 integration green.** The run holds **2/8 badges**, is in
**Slateport City** with the Devon errand's last delivery pending, and the team
is level-matched at 23-35 (it was 7-30 this morning).

Every bug this session had the same shape: **something that worked was being
scored as a failure**, and the loop reacted by abandoning the only path
forward. None of them were navigation or battle bugs.

1. **A successful sail read as six failures.** The step tested
   `FLAG_VISITED_SLATEPORT_CITY`, but Briney's boat lands on **Route 109**, the
   beach, and that flag only sets on walking north. Measured on a fork: after a
   crossing the player is on Route109 with `FLAG_HIDE_MR_BRINEY_BOAT_DEWFORD`
   set -- the last line of the sail script -- and the city flag still clear.
   Now two steps, because they are two things.

2. **The answer cancelled itself.** `advance_story` answers a question, runs
   the scene, then checks for another. Both used the path that presses A up to
   sixteen times to bring a box up. Correct for the first call; for the second
   those presses land in the cutscene the first answer started. The log read
   `chose SLATEPORT` then `could not pick 'SLATEPORT'`, forever. The follow-up
   probe is passive now.

3. **Giving up was permanent.** `_story_given_up` was a set. The sail was
   abandoned while Steven's letter was still undelivered -- correct at the
   time, since Briney genuinely refuses -- and never reconsidered after the
   loop went and delivered it. 233 battles in Granite Cave followed. Entries
   now expire after ten minutes and clear their failure counts.

4. **And the skip was silent.** Nothing in the log said why the story step
   was not being attempted. That is the whole reason (3) went unnoticed for
   half an hour.

**Mr. Briney's question is a multichoice, not YES/NO** (PETALBURG / SLATEPORT /
CANCEL). Answering YES picks PETALBURG -- back the way we came.
`Menus.select_label` resolves the index from `gMultichoiceLists`, the game's
own table, with the caller naming the whole expected box because SLATEPORT sits
at index 1 in one three-option list and 0 in another. Ambiguity is refused, not
guessed. `gMenu`'s bounds are leftovers until the box is drawn, so it reads as
an open menu while a message is still printing -- Briney's greeting needs eight
A presses first.

**Metrics** now project from two restart-proof clocks (the cartridge's
play_time and unix `at`), never `wall`, which resets per session and made one
badge gap negative. Distinct labels are counted, so a replayed fork cannot
shrink the estimate. `pokeagent.cli metrics` prints it from the event log with
no ROM required; the widget shows it under PACE with its basis attached
(`docs/pace-panel.png`).

## session port-06 -- badge 2 unattended, and four bugs found by looking (Aug 29 2026)

**359 tests green** (322 unit, 37 integration). The run took **badge 2** on its
own and is in Granite Cave with Steven's letter.

The theme of this session is that every bug below was found by *running the
thing and looking at the result*, not by reading code.

**The run.** The encoded Devon chain walked end to end unattended:
RUSTBORO_STATE 0->2, the Rusturf Tunnel grunt (a line-of-sight trainer whose
live position drifts from the map data), DEVON GOODS, Mr. Stone on DevonCorp
3F -- the step that unhides Briney -- then the ferry to Dewford and
**Brawly beaten**. Issue #31 closed by playing it.

**A run froze for fifteen minutes and nothing noticed.** Route 116, zero steps,
heartbeats every minute, frame counter climbing the whole time. My own party
promotion code had opened the party menu, landed on the SUMMARY page instead of
SWITCH, and ten B presses did not clear it. `advance_scene` did not recognise
the screen -- its task names are compiler-generated (`sub_8089D94`), so they are
now listed literally with a comment explaining why an opaque name must not be
tidied away.

The general fix is a **stall watchdog**: if position, battle count and badge
count are all unchanged for five minutes, recover in escalating steps. It
deliberately does not look at frames -- a wedged run burns 80k per heartbeat.
Seven tests cover the half that matters as much as firing: NOT firing, during a
long battle, a badge won standing still, or a slow stretch.

**Ho-Oh and Mr. Mime had no sprite.** Two implementations of one naming rule
disagreed: the generator named files after pret's directory (`mr_mime.png`),
the widget derives its request from the ROM display name and asks for
`mrmime.png`. Invisible -- a bad `Image.source` renders nothing and the row
falls back to text. Found by rendering the party offscreen and looking at it
(`docs/party-sprites.png`).

**The widget said `steps: 0`** for a run that had walked 54,382. The counter
only incremented on the grass-grinding path. Now reads
`gSaveBlock1.gameStats`, which the game keeps itself and which survives a
restart.

**A stride read from a C header instead of the linker map**, for the third time
in this project's history. `include/pokemon.h` declares `struct Evolution` as
three u16s = 6 bytes; the real array is padded to 8 (16480 / 412 = 40 per
species). It confidently answered "Shedinja: evolve REGIROCK at level 20".
Strides are now derived from the symbol's own size. `pokeagent/acquire.py` now
gives a real method for every achievable species -- stones included, off the
ROM's own `gEvolutionTable` -- except Deoxys, which is distribution-only.

**An hour lost to a warp in a corridor.** After badge 2 the run went into
Granite Cave and oscillated between 1F and B1F indefinitely. The player stood
on 1F at (17,12); a warp down to B1F sits at (17,11) directly above; the BFS
opened its route to the Route 106 exit with `U`, which fired the warp. Land on
B1F, plan back up, arrive next to the same warp, repeat.

It never stopped MOVING, so the stall watchdog written earlier the same session
could not see it -- position genuinely kept changing. The only log symptom was
`no approach to warp ... fired a map change`, which reads like a broken door
rather than a route that never arrives.

The rule the Gen-2 nav always had and this port lost: **a warp tile may be a
path's goal, never an intermediate cell.** Start and goal are both exempt --
the goal because routing to a door is the commonest thing the driver does, the
start because every door arrival leaves you standing on a warp (gotcha 15).
Verified live: B1F -> 1F -> Route 106, and the replanned route is the same
length as the broken one. The BFS is shared by every map, so this was one cave
away from being everywhere.

After the fix, in seven minutes: +7,231 steps, +44 battles (it had fought
none while oscillating), and the laggard promotion visibly working --
NINJA L7 -> L11, ROCKY L10 -> L11, EMBER L29 -> L30.

**Upstream.** `tests/integration/test_crystal_regression.py` drives Crystal
from power-on into the overworld and walks it; booting alone was never enough,
because navigation is where game-specific assumptions live. It caught a real
Gen-2 rule: the first press TURNS when not already facing that way.
`MERGING.md` measures the fork instead of describing it -- five of eight ported
modules byte-identical including the whole 567-line nav layer, three drifted
and all three for pokered support.

## session port-05 -- the team objective, actually working (Aug 29 2026)

**333 tests green** (301 unit, 32 integration). The run now builds a team
instead of KOing everything it meets: from the Stone Badge checkpoint it
catches, names, restocks and trains to parity without help.

Live, from one unattended session:

    party  EMBER L20 | SEA BIRD L9 | LOTTAD L8 | POOCHYENA L8
    spread 15 -> 12 (the L2 and L4 catches trained up to L8)
    gaps   9 -> 4 uncovered types
    caught 3, purchases 1

### What was missing

Objective 1 -- a well-rounded, level-matched team -- was the one requirement
with no implementation behind it. `Team.recommend_catch` scored candidates and
`BattleSession.throw_ball` threw balls, and NOTHING joined them: 604 battles
in, the party was one Pokemon with nine uncovered types.

`pokeagent/catching.py` makes the two decisions, which are genuinely different:
whether a wild is worth a ball (coverage the party lacks) and whether this is
the turn to throw (below a third HP, or immediately if our own best move would
KO it, because a fainted wild is gone). It WRAPS the training policy, so the
move that weakens is still the damage maths' choice.

One judgement worth stating: `recommend_catch` charges -0.25 per level a catch
owes the training floor. That is right for a settled team and wrong for an
empty one -- with a L24 lead every Route 102 wild scored about -5 and the run
refused them all. Below four members the parity term is dropped, because
training fixes a level gap and nothing fixes an empty slot.

### Seven bugs, all found by playing

1. **The catch was named AAAAAAAAAA.** `throw_ball`'s `_wait(press="A:2")`
   pressed through the "Gotcha!" text into the nickname keyboard -- this
   project's own stray-A bug, in a new place. `_wait` now stops for any
   keyboard, which protects every caller.
2. **The name came from the wrong mon.** The catch is not in the party yet when
   the keyboard opens, so `party[-1]` named a wild LOTAD "COMBUSKEN" after the
   lead. Reads `gBattleMons[1]`.
3. **The keyboard could not be driven.** Two causes: the first D-pad presses
   after it opens are swallowed (gotcha 2 applies to the D-pad, not just A),
   and the column walk wrapped THROUGH column 8 -- the OK strip -- where the
   new guard shoved the cursor back, the two fighting until the budget died.
   "BLAZE" typed as "BL".
4. **The Pokedex ate the run.** Catching a new species pops its dex entry; one
   more A opens the Pokedex ITSELF and every stall-press then navigates it --
   three minutes at 40k frames a step. `advance_scene` backs out of full-screen
   menus with B, and keeps pressing, because the field lock outlives the menu.
5. **A stale party menu wedged the battle loop forever.** Only findable once
   there was a second Pokemon to switch to. An interrupted voluntary switch
   leaves the menu up, and the forced-replacement path then picks a slot the
   engine never applies. `gUnknown_02038473 == 1` is the engine's own "this is
   a forced switch" flag (src/battle_party_menu.c:446) and it read 0.
6. **A refused action was retried forever.** The policy asked to switch to a
   slot the engine would not honour and, asked again, said the same thing:
   22 battles against one ZIGZAGOON, position frozen. Two identical failures
   now drop the policy for the rest of that battle, loudly.
7. **There was no way to buy anything.** The run hit level 20 with one Pokemon,
   an empty ball pocket and 5,130 in the wallet. `pokeagent/mart.py` drives the
   shop off `gMartInfo` and verifies every purchase against the bag and the
   wallet -- see below, because the engine taught me three separate lessons.

### The mart, and why it reads memory instead of the screen

`gMartInfo` (src/shop.h:28-38) gives `itemList`, `itemCount`, `cursor` and
`choicesAbove`, so `itemList[choicesAbove + cursor]` IS the highlighted item.
Three things had to be learned by tracing, each of which silently broke a buy:

* talking to a clerk opens BUY/SELL first; the item list only exists after BUY
  (`Shop_DoCursorAction`), and cursor presses before that drive the wrong menu;
* the quantity box does NOT open on the first A -- the engine walks two message
  tasks and only then hands input to `Shop_PrintPrice`, so early UP presses go
  nowhere and a request for ten balls bought exactly one. The count is now
  raised one press at a time and verified against `tItemCount`, the engine's
  own counter (src/shop.c:509);
* the confirmation on `Task_CallYesOrNoCallback` is the one real decision in
  the chain and is answered deliberately. Nothing answered it in the
  predecessor project, so purchases never happened while the code claimed
  success.

`is_open()` also has to include the field-control lock: `itemCount` SURVIVES
the shop closing, so a count-only predicate left the loop pressing B at an
indifferent overworld for an hour.

### Gotcha worth adding to the list

**Switching in Gen 3 swaps party slots.** After the training policy sent the
laggard in, `party[0]` was the L4 catch rather than the L19 lead -- which is
why the heartbeat appeared to show a level-4 mon leading, and why healing now
judges the WHOLE party rather than slot 0.

## session port-04 -- FIRST BADGE, and eight bugs found by playing (Aug 29 2026)

**STONE BADGE earned autonomously** (`saves/stone-badge.state`). The loop drove
a fresh starter through three story gates, trained to L16, crossed four maps,
walked into the Rustboro gym and beat Roxanne with no human input. 281 tests
green (265 unit, 16 integration). 30 GitLab issues tracked; 24 closed.

Everything below was found by RUNNING the thing, not by testing it. That is the
theme of the night: the unit lane was green through every one of these.

### The one that mattered most: the widget was costing 87% of the speed

Attaching `LiveFeed` dropped the emulator from **1028 fps to 12**. The same
1348-frame battle took 113.49 s instead of 1.31 s, and every real run has the
feed attached, so this was the effective speed of the project.

`after_slice` republishes on a clock, and the state half rebuilt the rich blocks
at 4 Hz: 311 ms a snapshot, 253 ms of it `Ladder.as_dict` reconstructing the
living-dex evolution chains. The feed was asking for 1.25 s of work per second
of wall clock. Those blocks change only when the party, badges or battle state
do, so they are now cached behind a cheap fingerprint. **1.35 s, 1000 fps.**
The frame PNG was never the problem: 0.7 ms.

### The rest, in the order they were hit

1. **`goto` did not notice a battle.** It replanned against a position that
   cannot change for its full 12-attempt budget -- 11 s -- then returned False
   saying "replan-cap reached", never mentioning the encounter that is the
   entire point of walking in grass. Now honours `on_battle` exactly as
   `travel` does. 0.79 s to raise.
2. **The loop had no heartbeat.** Its first overnight attempt printed five
   lines and then nothing for nine minutes; only the frame counter could tell
   "crossing a route" from "wedged". Reports position, frame, lead, battles and
   steps once a minute -- and immediately earned itself by exposing #1.
3. **The loop never pursued a badge.** 232 battles, level 19, fifteen minutes,
   still 0/8 badges on Route 101. Levels are not progress. `quest.py` now
   answers train / heal / travel / challenge from the badge count, with the
   level target read from the leader's OWN party and the leader's coordinates
   from the gym map's object_events. All eight resolve.
4. **A move-learn prompt outside a battle was answered by blind A presses.**
   Torchic evolves at 16 and Combusken learns DOUBLE KICK at 16, so that prompt
   arrives during the EVOLUTION scene. `advance_scene` presses A on a stall --
   it already refuses at a naming keyboard for this exact reason -- so the
   prompt ate one A, the forget-screen ate the next, and SCRATCH (40 power) was
   traded away while FOCUS ENERGY (0 power, status) survived. Reproduced twice
   independently before fixing.
5. **Movement failures did not say which gate stopped them.** "could not cross
   the L seam to Route104" cost most of a session in hand-grepping, twice.
   `gates.py` reads the coord_events that shut a road and greps the game's own
   scripts for what opens them, so the error cites itself with file:line.
6. **Static coordinates were used for acting.** Norman's map entry is (4,3);
   the gym is 9x112 with rooms joined by internal warps, and for his own
   introduction the game stands him at (4,107), four tiles from the door. The
   loop spent five minutes failing to reach a man standing next to it. Gotcha
   11 in a new costume. `live_cell` matches by local id (the map's Nth
   object_event is live local id N+1), which is exact.
7. **Routing planned exits it could not walk to.** Route 104 is two halves
   joined only through Petalburg Woods: from the south, 540 cells are reachable
   and none touches the northern border, so the listed U connection to Rustboro
   is real and unusable. Routing now filters by forward reachability (forward,
   because ledges are one-way) and routes over (map, landing-cell) nodes, so a
   map may appear twice -- which is the truth about Route 104.
8. **Every door read as a wall.** Rustboro's gym door has collision 1, as all
   doors do; they are entered, not walked through. "Is the warp cell
   reachable" therefore answered no for every building in Hoenn. `take_warp`
   always knew better, so reachability now asks the same question it does:
   the warp cell OR an orthogonal neighbour. Also, a seam is a whole border --
   crossing Route 104's north edge at x=22 lands in a 36-cell pocket while the
   road at x=11-19 lands on the road, and routing had been taking the middle
   candidate.
9. **The badge counter read a list of names as an int, and hid a win.**
   `badges()` returns `['BADGE01']`; `int(...)` raised; a bare `except` made it
   a permanent 0. So the loop beat Roxanne and then re-challenged her every
   step, logging "Roxanne not beaten yet (0/8 badges)" while standing next to a
   beaten Roxanne. A swallowed exception is worse than a crash: a crash cannot
   claim progress it did not make.

### Verified journeys (live, headless)

- fresh starter -> rival beaten on Route 103 -> Pokedex collected -> Route 102
  crossed -> Norman seen -> Route 104 -> Petalburg Woods -> Rustboro -> gym ->
  **Roxanne beaten**.
- Route104 (38,63) -> RustboroCity_Gym (5,18) in 124 s, a journey that could
  not complete at all before #7 and #8.

### Kanto went live, honestly

`pokered` builds with the vendored rgbds (21,128 symbols), so the three layers
that genuinely port -- symbols, charmap, emulator -- are now wired and
EXERCISED: Red boots, its screen decodes, and its main menu drives. `state` and
`nav` are not ported and raise by name, because a Gen-1 party read through
Gen-2 offsets parses cleanly and returns wrong stats, which this project ranks
as worse than a crash. `flat_party` was dropped from its capabilities: Gen 1's
party really is a flat struct, but nothing here reads it, and a capability
describes what the adapter can DO.

Two real differences surfaced by driving it rather than reading about it:

- **`wTilemap` vs `wTileMap`.** One capital letter between a decoded screen
  and a KeyError. Resolved against the symbol table actually loaded, so a
  build with neither name fails loudly.
- **pokered maps `$ed` twice** -- to the town-map up arrow at
  `constants/charmap.asm:85` and to the menu cursor at :177 -- and the parser
  keeps the first, so Red's ordinary NEW GAME cursor decodes as an up arrow.
  Same byte, different alias. The menu driver now matches all three glyphs and
  needs no knowledge of which game it is looking at.

### Known gaps

- **Badge 2 needs a boat.** Dewford is across water; Mr. Briney's ferry is
  another story gate and the PROLOGUE stops at badge 1. The stall guard means
  the run trains instead of wedging, which is the right failure.
- `talk_to` sometimes stops short in a gym when a trainer blocks the aisle; the
  loop retries across steps and gets there, but it is not deliberate about
  clearing interceptors first.
- The widget's STAGES section was written blind (no JS runtime on this box) and
  is **unverified visually** -- GitLab issue #20 is the standing task for that,
  to be done with the screen awake.

### Screen-off note

Everything above ran with `DISPLAY` and `WAYLAND_DISPLAY` unset. Proven, not
assumed: 265 unit tests, a booted Driver, and a written 240x160 framebuffer PNG
with both variables removed from the environment. Only the widget's own pixels
need a live compositor.

## session port-03 -- the nine requirements, plus a living dex and a ladder (Aug 29 2026)

**268 tests green** (252 unit, 16 integration). Everything the user asked for
is now either landed or filed as a GitLab issue; `scripts/sync_issues.py` is
the idempotent source of truth and filed 19 of them.

### What landed

- **Multi-game.** `gamespec.py` + `adapters/`: adding a game is data, with
  honest `live`/`declared` status. **Crystal is live** -- pokecrystal builds
  with vendored rgbds and PyBoy boots it, so Gen 2 and Gen 3 load in one
  process (Crystal 59,010 symbols / 436 maps / BFS; Sapphire 50,564 / 394).
  Gen 1's adapter refused to open at all at the time; it is live as of
  port-04 (see that entry) with its unported layers refusing by name.
- **Torchic**, from `GameSpec.starter`, so it is one fact in one place. The
  whole opening was regenerated: TORCHIC L5 SCRATCH/GROWL, nicknamed EMBER.
- **Living dex** (`living.py`) -- the clarified requirement. One of every
  species held AT ONCE, so a line costs one individual per stage: 100 lines,
  **187 individuals**, inside the 426 storage slots. Baby lines were the
  subtle bug: Pichu and friends are themselves in the No-Eggs group, so the
  breeding parent is the evolved form.
- **Stage ladder** (`stages.py`) -- complete game, living dex, then 5-IV /
  L100 / shiny as siblings, least-complete first so a run spreads effort.
- **Entropy** (`entropy.py`) -- reseeds `gRngValue` from urandom,
  /proc/interrupts, CPU jiffies, load, memory and the clock. Opt-in,
  journalled, suppressed during savestate searches, refused mid-battle.
- **The local model** (`brain.py` + `smallchoices.py`) with a boundary drawn
  from measurement: 5/5 on single-hop type questions, wrong on multi-hop and
  on numeric judgment, so it only breaks ties the maths declares equal.
- **Route guide** (`scripts/build_guide.py`) -- 693 trainer parties, 211 maps'
  story gates, per-map wild tables, 367 item sources, generated from the
  decomp.
- **Widget** -- renamed `poke.run`, Torchic logo, and a full dashboard:
  objective, living-dex bar, team levels and coverage gaps, opponent,
  counters, all optional keys.

### Verified visually

`docs/widget-bar.png` and `docs/widget-popup.png`: the Torchic mark and
"TORCHIC 20/20" in the bar; the popup showing "Pokemon Sapphire / GEN 3 .
HOENN", the objective, the live frame, the opponent with an HP bar, the
Pokedex bar at 1/188 and the team's coverage gaps.

### Bug found by using it

`scripts/play.py` kept routing into the Oldale Pokemon Centre 2F, because a
decorative metatile in there classifies as an encounter tile. The classifier
was right about the tile and wrong about the map; "does this map generate wild
Pokemon" is now gated on the ROM's own `gWildMonHeaders`. 70 maps, no indoor
false positives.

## session port-02 — the widget, seen (Aug 28 2026)

Short session. Confirmed the desktop was never actually broken: Hyprland PID
1152 from Aug 27 was alive and responsive the whole time. What existed were
two **stale** `$XDG_RUNTIME_DIR/hypr/` instance directories left behind by a
subagent's attempts to spawn a nested compositor for screenshotting, which
made `hyprctl` pick the newest signature and fail to connect. Removed them;
one live instance remains.

With the screen open, the last unverified claim is now closed. The widget
renders correctly in both states, and the popup screenshot is the best single
artefact this project has: the game frame, the party with a correct HP
colour, the ledger, and the agent narrating its own battle decisions with
reasons.

Screenshots are cropped to the widget alone — the raw grabs contained the
user's browser tabs and bookmarks and were not committed.

## session port-01 — the harness exists and plays (Aug 28 2026)

**Ported crystal-omp-agent to Pokémon Sapphire.** The harness boots the
user's own cartridge dump, drives a new game from power-on, walks the world,
reads decrypted party data, and wins a battle with damage predictions that
match the engine. 42 tests green (26 unit, 16 integration).

### Where the run stands

Working checkpoints in `saves/`, all reproducible from scratch:

| checkpoint | frame | meaning |
|---|---|---|
| `agent.state` | 7570 | fresh game, inside the moving truck |
| `littleroot.state` | 8414 | stepped out into LittlerootTown |
| `route101.state` | 93586 | Route 101, Birch's scene running |
| `starter.state` | 93904 | MUDKIP L5 taken from the bag |
| `first-battle.state` | 94048 | live wild POOCHYENA L2, battle data readable |
| `lab.state` | 102058 | Birch's lab, SWAMPY L5 17/21, player has control |

Regenerate all of them:

```sh
.venv/bin/python scripts/newgame.py  --state saves/agent.state --name RUBI
.venv/bin/python scripts/to_starter.py --state saves/littleroot.state
```

### What was verified, not assumed

- **The built ROM is byte-identical to the user's dump** (`cmp` clean), so
  the 50,963-entry symbol table describes the ROM we actually drive.
- **Headless throughput ~2,100 fps**, 35x realtime; savestates round-trip
  deterministically (replay after restore produces identical EWRAM).
- **The decoded map agrees with the engine 150/150** over random steps, and
  the integration lane re-runs a 60-step version every time.
- **Gen-3 substructure decryption works on real data**: MUDKIP L5,
  TACKLE/GROWL, checksum valid.
- **Damage predictions match**: TACKLE predicted 5-7, engine dealt 6 and 6.
- **The widget is installed and reading the feed** on this machine
  (`omarchy-shell sapphire.run status` answers with the live frame).

### Bugs this port produced and fixed

Kept because each one is a class, not an incident.

1. **Transcribed `struct BattleMove` stride.** Nine declared bytes, twelve-byte
   array element. TACKLE read as 0 power, SURF as 19% accuracy. Every stride
   is now derived from the symbol's size and must divide whole. This is the
   predecessor's "the harness lied more often than it crashed" category,
   reproduced on day one.
2. **`AAAAAAAAAA`.** Blind A-mashing typed the player name on the intro
   keyboard. Fixed by reading the keyboard's own ROM tables and the cursor
   sprite. Then it happened **again** one layer up, naming the starter, once
   `advance_scene` started pressing A — fixed by refusing to press A while a
   naming prompt is open.
3. **Unmasked nibble.** `currentElevation` shares a byte with
   `previousElevation`; reading the whole byte gave 51, which matches no
   tile, so every `goto` returned no-path.
4. **`advance_scene` returned mid-sentence**, because story scripts drop and
   retake the field-control lock between beats.
5. **`in_battle()` is true ~60 frames before `gBattleMons` is populated**, so
   a checkpoint saved in the transition read every species and level as zero.
   Added `battle_ready()`.
6. **`cstruct` mis-parsed pointer qualifiers** (`*const *` recorded a field
   called `const`) and **stopped at the first nested `}`**, so any struct
   opening with an anonymous union parsed as empty.
7. Six more inside the battle layer, listed in commit `3fc2844`: a phantom
   self-KO in the turn log, `free_hits()` masked by a heal, bag/party cursors
   swallowing presses between two fades, a forced post-faint switch claiming
   the party was wiped with a healthy mon benched, item lookups failing on
   the ROM's `POKé BALL` spelling, and `flee()` burning a turn instead of
   porting `CanRunFromBattle`.

### What Gen 3 gave us that Crystal never had

`gMain.callback2` and the `gTasks` entries are **function pointers**, and the
symbol table names them. "Which screen am I on" is an exact question:
`Task_NewGameSpeech16` *is* the gender menu, `Task_SetClock4` *is* the wall
clock's confirm box, `Task_StarterChoose2` *is* the starter picker and its
`data[0]` *is* the selection. The Crystal harness answered the same questions
by pattern-matching decoded screen text, and its `keyboard_open()` is still
literally `"DEL" in screen and "END" in screen`.

Likewise `sLockFieldControls` (`src/script.c:179`) is the engine's own "the
player may not move" flag — the thing Crystal inferred from position not
changing, which is the root of a whole failure class in its journal.

### Session addendum -- the remaining Crystal modules, and four more bugs

Ported the rest of the original's surface: `missables.py`, `rolling.py`,
`schemas.py`, `autopilot.py`, plus `heal` / `heal_at_nearest_center`, which
the first pass simply did not have. **101 unit + 16 integration tests green.**

`missables` answers the question that cost the predecessor a whole
playthrough on foot: HM02 FLY resolves to Route119 (25,31), five caller hops
up through a coord_event, and `status()` now carries a `missing:` fragment
so the fact is where a session already looks.

Four defects found by using the harness rather than testing it:

1. **`press` was in the action table with no Driver method.** Dead on every
   surface -- the CLI, serve and autopilot all raised. Found by the autopilot
   port actually calling it. Audited the whole table afterwards; no other
   verb is missing.
2. **`travel()` had no answer for a wild encounter.** A battle on a grass
   route made `goto` refuse (scene lock) and the failure surfaced as the
   nonsense "could not cross the U seam". Now a scene is advanced before each
   leg and a battle raises `TravelInterrupted` carrying where and why --
   movement must never silently auto-fight, so `on_battle="fight"` is opt-in.
3. **`_cross_seam` tried unreachable edge cells**, sorted only by distance.
   Now intersected with `nav.reachable`.
4. **`talk_to` could not reach anyone behind a counter.** Pokemon Centre
   nurses and shop clerks stand behind an `MB_COUNTER` tile that cannot be
   stepped on; you talk across it. `heal()` reported "nothing answered an A
   press" until the approach cell moved one further out.

Proof it holds together: from `lab.state`, `heal_at_nearest_center()` routes
out of Birch's lab, fights through two wild encounters on Route 101, crosses
the seam into Oldale, enters the Centre, talks across the counter and comes
back 23/23. Checkpoint `oldale-healed.state`.

Module parity with the original is complete but for `hookevents`, which is a
deliberate non-port: PyBoy PC hooks existed to detect text pages, and Sapphire
answers that question exactly through `gTasks` / `gMain.callback2` /
`sLockFieldControls`. See README.

### Known gaps

Honest list; none of these are claimed as working.

- **The level-up move-learn prompt is unexercised.** Detection is proven
  statically (`gBattleScriptingCommandsTable[0x5A]` resolves to
  `atk5A_yesnoboxlearnmove`) and `default_learn` is unit-tested, but no live
  level-up is reachable from the current checkpoints — a L5 Mudkip does not
  level off a L2 Poochyena.
- **Double battles.** Cursor and battler indexing are written for
  `MAX_BATTLERS_COUNT` and read the menu battler rather than assuming 0, but
  only singles have been driven. Spread-move damage halving is deliberately
  omitted rather than guessed.
- **Trainer battles, badge boosts and weather.** Ported and gated correctly,
  never exercised — the only live fight so far is a badge-less wild one, so
  `badge_boost_applies()` has only ever returned False.
- **The move-learn prompt is still unexercised.** Grinding to L8 on Route 101
  learned MUD-SLAP into a free slot, which does not prompt. The prompt needs
  a full moveset -- Mudkip at L15 -- and the grind stopped on low HP before
  reaching it.
- **No progress past Oldale.** No gym, no HM field moves, no shopping, no
  PC/box handling, no catching outside the one verified ball throw.
- ~~The widget's pixels were never seen.~~ **Closed.** Verified visually
  with the session open: `docs/widget-bar.png` and `docs/widget-popup.png`.
  The bar shows a dimmed gamepad when idle and `SWAMPY 22/26` while running;
  the popup shows the live framebuffer, a green HP bar (85%, so `hpColor`
  bands are right), the ledger, and narration carrying the harness's own
  reasoning. The staleness branch renders too.

### Next objective

Play. Everything above is scaffolding; the harness has not yet been used to
do anything hard. In order:

1. Route 102 -> Petalburg -> Route 104 -> Rustboro, catching along the way.
   This exercises `catch`, shopping, and the first real trainer battles.
2. **ROXANNE** (Stone Badge). Fork `pre-roxanne` first. That is the first
   test of `tactics.recommend` against a trainer with a real party.
3. First HM (CUT) — the field-move path does not exist yet and will need
   writing, not porting.

## session lead — dex 99 -> 111, eight parallel hunters (Sep 5 2026)

**Canonical: `saves/line3.state` = dex 156/178 (87.6%), 0 junk nicknames.**
clean field state.** Milestones this session, each a NEW filename:
`milestone-dex105.state`, `milestone-dex107.state`, `milestone-dex108.state`,
`milestone-dex111.state`, `milestone-dex113.state`, `milestone-dex115.state`,
`milestone-dex132.state`, `-dex133`, `-dex134`, `-dex139`, `-dex145`,
`-dex146`, `-dex148`, `-dex151`, `milestone-dex156.state`.
Fork from the newest;
never drive `line3.state`
directly (`pyre_shoal.py` refuses it on purpose).

### What landed
| species | how | cost |
|---|---|---|
| GLOOM, SEADRA, BEAUTIFLY, SWALOT, ... | `share_loop.py` EXP.SHARE bench grind | hours, unattended |
| BELLOSSOM | `stone_evolve.py` (Sun Stone already in the bag) | 2 min |
| MAGNEMITE, VOLTORB | New Mauville (`newmauville_hunt.py`) | 50 min |
| LUDICOLO, RAICHU | `shard_trade.py` — shard balls need DIVE | 11 min each |
| IGGLYBUFF, PICHU | `breed.py` | ~35 min each |
| CLAYDOL, BANETTE | `skypillar_grind.py` | 12 min |
| CRAWDAUNT | evolved itself at L30 walking off the pillar | free |
| VULPIX, DUSKULL, CHIMECHO | `pyre_shoal.py --legs pyre` | 13 min |
| RHYHORN, PINSIR | `safari_hunt.py --area nw` | 9 min |
| HERACROSS, PHANPY | `safari_hunt.py --area ne` | 10 min |
| +17 more (dex 115 -> 132) | `share_loop.py` unattended, 131 laps | overnight |

### Harness bugs fixed (all were mine, all found by playing)
0. **The "A" names: cause fixed, and then 59 hidden ones found.** The cause
   was `accept()` (below). But `DexTarget.boxed()` never filled in the
   nickname -- `parse_mon` leaves it to the caller and this caller was not
   one -- so every boxed mon read as `''` and an audit for junk names
   returned zero from the boxes *no matter what was in them*. The real count
   was 59. `scripts/fix_names.py` clears them in one pass, writing the field
   directly: the secure block starts at `0x20` and the checksum covers only
   it, while the nickname sits at `0x08` in the plaintext header, so a rename
   cannot touch species/EXP/IVs/moves. **The frozen count is the proof the
   naming bug is dead**: exactly 59 on dex105, dex111, dex115 and dex132, so
   the 17 species caught after the fix branded none. `nickname` is a
   FIXED-WIDTH 10-byte field, so a 10-character name (WIGGLYTUFF) is written
   unterminated rather than overflowing into `language`.
1. **`naming.py accept()` was TYPING the letter "A".** It sent `START` then
   `A` to take the pre-filled species name, but START is swallowed during
   menu setup (gotcha 2), so the `A` typed a character. That is the source
   of every mon named "A" in this repo's history. `accept()` now presses OK
   by position, verifies the buffer, and clears a typed char before
   retrying. A wedged save with tasks `['Task_HandleInput','Task_80B64D4',
   'Task_NamingScreenMain']` and "every step_dir refused" is this bug.
2. **`trek.at_title()` blacklisted the overworld**, i.e. "the callback is not
   CB2_Overworld", which is true of every non-overworld screen *including the
   nickname keyboard*. `unwedge` therefore announced "on the title screen --
   taking CONTINUE" at a naming prompt and pressed A blindly. It now
   whitelists the four real boot callbacks (`MainCB2_Intro`, `MainCB2` with
   `Task_TitleScreenPhase*`, `CB2_MainMenu`).
3. **`stone_evolve.py` spelled THUNDERSTONE with a space.** Guarded now.
4. **Title-screen wedge after a Champion win.** Winning enters the Hall of
   Fame, the credits roll and the game SOFT-RESETS to the title. Every
   `step_dir` is then refused and `goto` says "stalled Nx". `Driver.at_title()`
   / `resume_from_title()` recover it; `settle()` calls them.
5. **Real-hardware pacing.** `SAPPHIRE_FPS=hardware` throttles `tick()` to
   59.7275 Hz against an injectable clock. Measured 1322 fps unthrottled vs
   59.7 paced.

### Traps worth knowing before you route anything
- **A game-clear `setmaplayoutindex` rewrites map collision.** Route131 (Sky
  Pillar) and Route130 (Mirage Island) both do it, so nav decodes the shipped
  layout and calls a post-game map sealed. `sync_grid()` is the whole fix —
  it reported exactly the 233 changed cells predicted offline.
- **Route 124's shard balls are NOT surf-reachable.** Three lagoons walled by
  collision=1 reef touching no map edge. nav's "no-path" was correct. You
  need DIVE; nothing in the canonical party learns HM08, but LOMBRE in box 0
  already knows it.
- **`give_to_mon` only lands with the party reduced to two.** The party
  picker index is not the party index, so `share_grind` shrinks the party
  first. This is why AZURILL is still blocked: it needs a held SEA INCENSE
  (`daycare.c:602-622` silently rewrites it to MARILL otherwise), and the
  incense is in the bag on `saves/pyre-incense.state`.
- **Shoal Cave's tide is the host wall clock** (see the dex-111 commit).
  Low tide is hours 3-8 and 15-20 local; SNORUNT is unreachable outside it.
- **Never let a background job hold the canonical save.** An `elite_four.py`
  run finishing after a promotion wrote the *old* line over `line3.state` and
  silently reverted dex 107 -> 105. Milestones are what saved it.

### Session two: 132 -> 156 with twenty hunters and one chain

Twenty subagents ran across two batches. The first batch banked thirteen
species on eight forks I could not merge; the second was given a different
contract -- **deliver a chain LEG, not a save** -- and integration became a
chain run instead of a promotion dance. That contract change is the single
most useful thing to carry forward.

Species this session: BALTOY, CACNEA, TRAPINCH, BELDUM, SPHEAL, KOFFING,
SKITTY, NOSEPASS, NINETALES, STARYU, STARMIE, RELICANTH, FEEBAS, WAILORD,
SEALEO, METANG, CACTURNE, VIBRAVA, WEEZING, SPOINK, ABSOL, CORSOLA, SNORUNT,
CROBAT, SHEDINJA. Banked on forks and reproducible as legs but not yet on the
canonical line: RAICHU, DELCATTY, AZURILL, IGGLYBUFF, PICHU, LATIAS.

**19 actionable species remain.** Eleven are by-level evolutions the grind
engine closes on its own once it runs long enough (FLYGON, GLALIE, GRUMPIG,
MACHOKE, MAGNETON, METAGROSS, SALAMENCE, WALREIN); the rest are the three
Regis, RAYQUAZA, and the six already-proven legs above.

### What the hunters found that the harness was getting wrong

Nine library bugs, every one of them discovered by playing rather than by
reading:

| bug | cost before it was found |
|---|---|
| `goto` fought encounters itself, bypassing `on_battle`/`Collector.fight` | 2 SPHEAL and a NOSEPASS surfed to 0 -- **my regression** |
| `state.enemy_party()` returned `[]` in EVERY wild battle (`gEnemyPartyCount` is only written by `CalculateEnemyPartyCount`, which the wild path never calls) | a catch policy fell through to attacking and killed a dex-new NINCADA |
| `flight.step_outside()` had no underwater branch | four separate hunts hand-wrote the same escape; canonical sat parked underwater poisoning every leg that opened with a fly |
| `travel()` leaked `_journey_deadline` out of all eleven `raise` paths | an expired deadline made every later walk fail instantly |
| nav modelled only `MB_MUDDY_SLOPE`, not `MB_BUMPY_SLOPE` or the four rails | nav promised all 34 Jagged Pass grass cells; the true on-foot count is ZERO |
| `boulder_solver` wrote a refusal-without-STRENGTH to disk as permanent | poisoned the only route to the Ice Room, for every agent, forever |
| `stone_evos.settled_enemy` waited for a menu by ticking | spun 30 minutes on one battle, printing nothing |
| `DexTarget.boxed()` never filled in nicknames | hid 59 mons wrongly named "A" and broke matching by name |
| `dex.py` keyed the fossil pair off `caught \| seen` | Steven's CRADILY made both lines look taken, so the target advertised two impossible species |

**The pattern worth keeping: every one of these was silent.** None raised;
each returned a plausible-looking wrong answer. `+0` from a hunt is not
evidence of absence until you have seen its stderr.

### Still open, with the blocker named
- **The three Regis.** The chamber is now openable for the first time --
  RELICANTH and WAILORD are both caught and TM28 DIG is collected and taught
  (the canonical line had never picked up TM28, and no mon knew DIG, so it was
  strictly impossible before). The blocker is Route 134's WATER CURRENTS:
  `player_step` runs `TryDoMetatileBehaviorForcedMovement` every frame whether
  or not a key is held (field_player_avatar.c:265-278, :329-336), so the
  avatar drifts one tile west between the press and the position read and
  every planned route desyncs on its first leg. nav cannot express this; a
  drift-tolerant greedy rider is the shape that works.
- **RAYQUAZA.** Route to SkyPillar_1F is proven (sync_grid reports exactly 233
  changed cells on Route131). The crumbling floors are gated by a COLLAPSE
  TIMER, not entry speed: `data[4]=3` on entry, rewrite to
  `CRACKED_FLOOR_HOLE`, and the hole branch zeroes on dest coords every frame
  ungated (field_tasks.c:663-667, :684-703). The "must enter at speed 4"
  rule in circulation is wrong, and so was the first agent's "no route" --
  they retracted it themselves.
- **No MASTER BALL exists on this line.** `ITEM_MASTER_BALL` appears nowhere
  outside `pret/data/scripts/debug.inc`. Weaken and throw Ultras.

### The integration tax, and the thing that finally beat it

Twelve parallel hunters banked thirteen species and I could promote exactly
ONE of them, because a savestate is a whole-machine snapshot and two forks
cannot be merged. That is the single biggest cost in this project.

`scripts/chain.py` is the answer: every hunt script takes `--state`, mutates
it IN PLACE, and skips species already flagged CAUGHT, so they COMPOSE.
Point them all at one file in sequence and the species accumulate on a single
line. dex 134 -> 148 came out of three chain runs plus the grind engine.

Three bugs made the chain look like four broken hunts before it worked, and
all three were mine:

1. **Position.** Every leg inherits the last leg's position. The BELDUM leg
   ends inside Steven's house, the SHOAL leg four warps deep in a cave, and a
   hunt that opens by flying is refused outright when indoors -- so it exited
   before hunting and the chain logged `+0`. `normalize()` now walks outside,
   flies to a hub and heals between legs. `fiery` went +0 -> +1 and `skitty`
   0 -> +2 on that change alone.
2. **The chain owned the live feed.** `Driver(state)` AUTO-ATTACHES a feed
   named after the save's stem and holds the claim for the process lifetime,
   so reading the dex in-process made the CHAIN the owner of
   `live/<stem>.owner`. `LiveFeed._claim` hard-errors on a claim held by a
   live process -- rightly -- so any leg building a feed EXPLICITLY died in
   under a second. Legs relying on `_autofeed` survived because it swallows
   the failure, and that asymmetry is why one root cause looked like four
   unrelated script bugs. `dex_count` and `normalize` now run in subprocesses.
3. **Silence.** The runner swallowed stderr, so three instant failures read as
   `+0` -- "no species here" -- which is the most expensive kind of silence
   because it looks like an answer. A nonzero leg now logs its stderr tail.

**The lesson worth keeping: `+0` from a hunt is not evidence of absence until
you have seen its stderr.**

### Evolution is usually cheaper than hunting
MAGNETON is a flat 1% slot in New Mauville (~1800 steps, and the leg failed
twice on routing) -- or +3 levels on a MAGNEMITE the save already owned. Check
`t.wild.for_map()` against the evolution table BEFORE routing anywhere: once
the bases were caught, nine of the remaining species became by-level
evolutions the unattended grind engine does for free.

### The Safari Zone is DONE -- check before you hunt
All four quadrants owe dex-115 **nothing**. I learned that the expensive way:
two full Southwest trips (~35 min, 1000 steps, 1000 money) chasing SEAKING on
the strength of a peer's "SEAKING and GOLDUCK did NOT land". That was true of
THEIR fork and false of this line, where both were already registered.
`safari_hunt.py` now refuses a quadrant that owes the loaded save nothing.

**The general rule this cost me:** a fork-relative claim about what is
missing is not evidence about the canonical state. Eight agents on eight
timelines each report "X is still missing" truthfully about themselves and
wrongly about you. One cheap read settles it:
```python
owed = {names.species(r.species) for r in target.wild.for_map(MAP)
        if r.species in set(target.missing())}
```

### Next
63 to go, of which 16 are unreachable by design (7 version-exclusive,
6 trade-evolution, 3 event-only), so **47 are achievable**. Cheapest first:
`pyre_shoal --legs shoal` (SPHEAL now, SNORUNT at low tide), `safari_hunt`
(HERACROSS, PHANPY), `breed.py --baby azurill` once the incense equips.
