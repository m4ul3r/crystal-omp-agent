# FUCK_I_MESSED_UP.md — session `claude` (Aug 26 2026)

Running log of everything that bit me, in the order it bit me. Each entry:
what I did, what actually happened, what the real cause was, and what I did
about it. Harness bugs I fixed are marked **[fixed]**; ones I only worked
around are **[open]**.

---

## 1. `scripts/newgame_bedroom.py` does not run against this build **[fixed]**

`TypeError: Driver.__init__() got an unexpected keyword argument 'fresh'`.

`AGENTS.md`'s capabilities map documents `Driver(state, fresh=True)` (power-on
reset, no savestate) and the script also passes `live={...}` and calls
`d.live.note(...)`. Neither exists in `trek.py` as checked out:
`Driver.__init__(self, state_path=None)`, and `dir(Driver)` has nothing
matching `live`.

- Fixed the documented half: `Driver(state_path, fresh=False)` now skips the
  savestate load (`Crystal(..., None)`) so a fresh power-on boot works.
- Did **not** rebuild the LiveFeed plumbing at the time (nobody was watching)
  and forked `scripts/newgame_claude.py`, the same intro driver without
  `live=`. **Later corrected (#54): the LiveFeed now exists, the fork is
  deleted, and `newgame_bedroom.py` runs as documented.**

**Lesson:** the docs describe a superset of this checkout. Verify an API with
`inspect.signature` before trusting a doc table. But "the docs lie" was only
half the story: the docs described a feature whose PUBLISHER was fully written
and merely unattached, and forking a script around it kept it dead for another
six sessions (#54).

## 2. `d.menu.resolve_choice('YES')` — wrong object **[open, docs]**

`goto`'s own failure message says
`resolve_choice('YES') if answering is safe (gotcha 13)` and `AGENTS.md` lists
it under "Menus anywhere" (`d.menu.…`). It is `Driver.resolve_choice`;
`Menus` has no such method. Cost one `AttributeError`.

## 3. `resolve_choice` cannot answer a YES/NO box drawn over the overworld **[open]**

Mom's clock scene (`SUNDAY, is it?`) →
`{'answered': False, 'note': 'no choice cursor settled on screen'}` with
`options` like `'▃▄◖▛▛◪▃▄▂▂▂▂λλ│ YES'`. The label rows are the *whole screen
row*, so the map glyphs to the left of the box are concatenated onto the
option text and the cursor scan misses.

**Workaround used all session:** read the cursor myself —
`'▶YES' in row` / `'▶NO' in row` — then `U`/`D` + `A`. That is what answered
the day / DST / time / phone questions.

## 4. Elm's starter came out named "CYNDAQUIL", twice **[fixed]**

The persona's starter is supposed to be **PANIC**.

`flush_dialog` handles a naming keyboard by calling `dismiss_keyboard()` with
**no name** — which confirms the empty/default name. `_pending_nickname` was
only ever consulted by `fight()` / `catch()`, so there was *no* path to name a
**gift** Pokémon (starter, Togepi, Eevee, a hatch) as long as a dialog flush
owned the loop. `name_prompt()` exists for this, but it can only be called
while the keyboard is already up, and the flush eats it first.

- Added `Driver._take_pending_nickname()` and used it on both keyboard
  branches of `flush_dialog` (hook-driven and legacy). Arming
  `d._pending_nickname = "PANIC"` before triggering the gift now names it.
- Verified live: party is
  `{'species': 'CYNDAQUIL', 'nick': 'PANIC', 'level': 5}`.

Cost: two full replays of the intro from `saves/claude.state`.

## 5. `type_name` typed into a keyboard that was still sliding in **[fixed]**

First attempt at naming the starter: `d.keyboard_open()` was `False` while a
letter grid was plainly on screen, and `type_name` typed anyway — every press
was dropped and `wNamingScreenCurNameLength` read back **38** (garbage).

`type_name` now waits (up to ~400 frames) for `keyboard_open()` and logs a
warning if it gives up instead of typing blind.

## 6. `keyboard_open()` is screen-text-only and unreliable **[open]**

It requires both `DEL` and `END` to decode on screen. During the starter
prompt the right-hand columns of `screen_text()` decoded as `▁▁▁▁` filler, so
it returned `False` for many seconds. `_naming_sig()` (WRAM
`wNamingScreenType`/`DestinationPointer`, which `flush_dialog` already uses)
is the reliable detector — `keyboard_open` should consult it too, otherwise
`name_prompt`'s precondition can refuse on a keyboard that IS up.

## 7. Self-inflicted: setting `d._naming_busy = True` silently kills all input **[open, mine]**

I set it by hand to "lift the freeze" while poking a stuck screen. It is the
opposite: `_naming_busy` marks *naming in progress*, so `press()` swallows
every other caller's input. I then spent three tool calls concluding the
emulator was frozen — frames advanced, the picture never changed, `A` did
nothing. Raw `pyboy.button_press` proved input was being dropped by the
harness, not the game.

**Lesson:** never poke `_naming_busy`; it is owned by `type_name` /
`dismiss_keyboard`.

## 8. The letter-grid "naming screen" was a decode artifact **[open, mine]**

The 7×7 box of alphabet glyphs I read as "the naming keyboard" was a textbox
mid-draw with unloaded tiles — the actual pending prompt was
`CYNDAQUIL, the fire POKéMON?` YES/NO. Exactly `AGENTS.md` gotcha 11: screens
decode as glyphs, not semantics. Decide from WRAM and structured calls; use
screen text only for the dialog rows, which *do* decode reliably.

## 9. `flush_dialog` reports `"menu"` on overworld glyph rows **[open]**

Several flushes returned `"menu"` with `last_choice_options` full of map
glyphs (`'√∝∞∟▖▖…'`) and no box on screen. The cursor-glyph scan false-positives
on overworld tiles. Harmless if you re-read the dialog text and keep pressing
A, but it means `"menu"` is not trustworthy evidence that a choice is open.

## 10. `talk_to` picked the sealed side of the Poké Ball **[open]**

`d.talk_to(6, 3)` (Cyndaquil's ball) routed to `(6, 2)` — behind the ball,
where Prof. Elm at `(5, 2)` severs the path — and gave up with
`blocked-by-stationary-npc`. `d.goto(6, 4)` + face `U` + `A` worked first try.
An earlier call in the same session *did* pick `(6, 4)`, so the side choice is
order-dependent, not deterministic-by-geometry.

## 11. Nav treats `coord_event` cells as sealed, so exits vanish **[open]**

Three separate dead-ends, same cause. `map_view()` shows `!` on the cells a
scene trigger sits on and `goto`/`travel` refuse to path through them, even
though `tile_at()` calls the same cells `floor`:

| where | cells | what it cost |
|---|---|---|
| `ELMS_LAB` | `(4,8)`, `(5,8)` — the aide's Poké Ball/Potion `coord_event`s | `no path from (4,2) to (5,10)`; could not leave the lab, twice |
| `ROUTE_29` | `x=53`, rows 8-9 — the catch tutorial | `TravelError: leg 4: no path to any approach`, aborted the whole journey |
| `SPROUT_TOWER` | assorted | repeated `unreachable` inside the tower |

Manual `step_dir` straight through them works and fires the scene, which is
the intended behaviour. **Workaround:** when `goto` says `unreachable` on an
indoor map, step the column by hand.

## 12. `talk_to` walks BEHIND a target and strands itself **[open]**

Elm's lab: `talk_to(5, 2)` routed to `(5, 1)` — the one-tile alcove north of
Elm — and then nothing could path out (`no path from (5,1) to (5,11)`), because
Elm's own body is the only exit. Escaped with two hand `step_dir` calls.
Same shape as #10: the approach side is chosen without asking whether it is a
trap.

## 13. `mart_buy` never gets past BUY/SELL/QUIT **[open]**

`d.mart_buy(1, 3, 'POTION', 6)` in `VIOLET_MART`:
`shop menu slow to open; settling and re-talking` twice, then
`RuntimeError: ... (bag 0 -> 0, bought=False)`. The clerk in GSC answers with a
**BUY / SELL / QUIT** menu; `mart_buy` waits for a `¥` row (the item list) and
never selects BUY. Feeding it a pre-opened list (`talk_to` +
`menu.select_label('BUY')`) got further but still bought nothing — it reported
`bought=True` with money unchanged, i.e. the list navigation lies.

Bought by hand instead (`D` to the POTION row, `A`, `U`×5, `A`). Note the
quantity picker read `×05` and the bag gained **7** Potions for ¥2100 — the
picker presses do not land 1:1, so verify from the bag, never from the glyph.

## 14. `take_warp` still mis-sides south-wall doors **[open, known]**

`VIOLET_MART` exit `(2,7)` and `SPROUT_TOWER_1F` exit `(9,15)`:
`entered (9,15) from (8,15) but the map is still SPROUT_TOWER_1F`, retried
forever, and `travel` failed the leg. This is the same R,L,U,D ordering bug
`PROGRESS.md` already xfails for Kurt's door. `goto` the cell *above* the door
plus `d.step_hold('D')` works every time — that is what I used for every door
for the rest of the session.

## 15. False "naming keyboard" detection mashes START+A into the overworld **[fixed]**

Every cutscene and every evolution printed 30+ `naming keyboard: confirming`
lines. `flush_dialog` treats any `_naming_sig()` delta as a keyboard, and those
bytes ($c6d0-$c6d8) are unioned with other screen buffers, so a scene
scribbling through them looks like a naming screen. `dismiss_keyboard` then
presses `B`×12 + `START` + `A` — into the overworld. Twice that opened the
**START menu** and walked into the **Pokédex**, after which `wScriptMode`
stayed 1, all movement was "blocked", and I burned several calls diagnosing a
"stuck script" that was really an open menu (gotcha 7, self-inflicted via the
harness).

Fixed in two layers:
1. `_naming_screen_plausible()` — reject values a real `NamingScreen` could not
   have written (`wNamingScreenType < 8` per
   `constants/menu_constants.asm:129`; name lengths ≤ 10).
2. `_naming_opened(sig0)` — after a plausible delta, wait ~80 frames for an
   actually rendered `DEL`/`END` row before confirming; otherwise re-baseline
   and carry on. A real keyboard is patient, so waiting is free.

Verified live: the whole Route 29 → Cherrygrove → Route 30/31 → Violet trek,
including Quilava's evolution, ran with **zero** spurious confirms.

## 16. `close_menus()` does not escape the Pokédex **[open]**

After #15 dumped me in a dex entry, `close_menus()` returned without closing
it (`wScriptMode` stayed 1 and every step read `blocked`). Five separate
`B:4 .:25` presses were needed. `close_menus` should loop until the overworld
is actually back, and say so when it cannot.

## 17. `step_dir` reports `blocked` for a step that happened **[open]**

`SPROUT_TOWER_3F`, at `(11,9)`: `U` → `blocked` three times, `observe()['tiles']`
said `u: floor`, then a single `L` returned `moved` and the position became
`(11,8)` — y decreased, x unchanged. So the earlier `U` did move and the return
value was wrong, and the `L` return described the wrong step. Any loop that
trusts `blocked` to mean "wall" will give up on an open corridor (mine did).

## 18. `catch` throws no ball and quietly KOs the target instead **[open]**

`resolve(d, 'catch', {'nickname': 'CACHE'})` on a L5 Bellsprout:
`menu: wait_for: predicate never true in 2000 frames` ×2, then
`[battle] ('ball','POKE BALL') changed nothing for 2 turns: substituting
'attack'` — and Ember killed the thing I was trying to catch. The substitution
is a reasonable anti-hang guard for *attacks*; for a ball it destroys the
objective and should abort instead.

Related: the first, accidental catch (the encounter policy from a previous
`pace` was still `'catch'`) DID work, but the post-catch nickname prompt was
declined because nothing was armed, so the party now holds a plain
`BELLSPROUT`. The `menu: wait_for: predicate never true in N frames` line
appears in nearly every battle in this session; it is noise at best and a
broken menu wait at worst.

## 19. Two unit tests are red on a clean tree **[fixed in #54]**

`tests/unit/test_live_feed.py::test_tick_slices_and_renders_only_the_owed_frame`
and `::test_tick_slice_never_overshoots_the_request` fail with
`AttributeError: 'Crystal' object has no attribute 'observe'`. Confirmed
pre-existing by stashing my `trek.py` changes and re-running. `crystalagent/
live.py`, `scripts/newgame_bedroom.py` and that test file are all **untracked**
— a half-landed LiveFeed feature (see #1). Everything else: **593 passed**.

**Closed in #54, and I was wrong to shelve it as "not mine".** The
publisher was complete; `Crystal` was missing a 12-line observer hook. I
carried these two failures through seven journal entries as background
noise instead of reading the traceback once.

---

# Part 2 — the run for the Elite Four (same session, badges 2-3)

Verdict up front: **the Elite Four was not reached.** Three badges
(ZEPHYR / HIVE / PLAIN), HM01 CUT, HM05 FLASH, SQUIRTBOTTLE. What stopped
it was not the game — it was ~10 more harness defects, three of which cost
whiteouts and one of which burned 60 wild battles for zero experience.

## 20. `train()` is unusable: an infinite heal rail, then zero exp **[open, P0]**

`d.train(20, targets={...})` on ROUTE_34, two separate runs:

1. **First run: 30+ consecutive Pokécenter round trips without a single
   battle.** Every iteration logged `train: healing rail (QUILAVA 65/65)`
   and walked Route 34 -> Pokécenter -> Route 34 again. Cause: the party
   held the **Togepi EGG**, and a resting egg reads **0 HP**. `train`'s
   heal check treats that as a fainted party member — the exact trap
   `HANDBOOK.md` warns about ("a resting egg shows 0 HP — that is NOT a
   fainted mon"), just not honoured in `_train_heal`. It never terminates,
   because healing cannot fix an egg.
2. **Second run (egg hatched): 60 battles, `party min L5`.** The rotation
   puts the weakest mon in front, but a L5 TOGEPI knows only GROWL/CHARM
   and a L5 BELLSPROUT only VINE WHIP, against L10-12 wilds. Nothing gets
   KO'd, so nothing gains exp — 60 battles, four levels total, all on the
   carry. It also healed after nearly every battle (~25 more round trips).

Both need fixing before any future run tries to build a team:
skip egg slots in the heal check, and refuse to rotate in a mon that
cannot damage the local encounter table.

## 21. A policy exception is silently downgraded to "declined", and the
harness then picks **slot 0** — usually a status move **[open, P0]**

`[fight] auto: attack slot 0 (GROWL) (policy declined this turn)`.
My policy raised `AttributeError` (`battle_frame()` returned `None` and I
called `.get` on it) and later `KeyError: 'name'`. Neither was reported as
an error — both looked identical to "the policy had no opinion", and the
fallback is *slot 0*, which for most of my party is GROWL / LEER / DEFENSE
CURL. Whole battles were fought with status moves.

A declined turn and a crashed policy must not look the same, and the
fallback should be "the best damaging move", never slot 0.

## 22. `outlook()['moves']` is NOT in battle-slot order **[open, P0]**

I indexed `('attack', i)` with `i` from `outlook()`'s list. Result: LEER
picked as the "best move" against Whitney's Clefairy, twice, and a
whiteout. `d.last_battle.summary()` is what caught it:
`T1 me attack:1 | enemy#CLEFAIRY 53->53`.
Fix in my code: score by move **name**, then map the name back to the live
`battle_frame()['moves']` slot. The harness should either document the
ordering or return the slot index in each outlook row.

## 23. `use_item` cannot see KEY ITEMS **[open]**

`observe()['bag']` lists `SQUIRTBOTTLE: 1`; `d.use_item('SQUIRTBOTTLE',
field=True)` -> `False`, `last_item_reason='not-in-bag'`. `bag_item_index`
only searches `wItems`/`wBalls`, never the key-item pocket, so no key item
can be used through the API — that blocks SQUIRTBOTTLE (Sudowoodo),
SECRETPOTION, the CARD KEY, and every other quest item.

Workaround that worked: `START` -> PACK -> `R` twice to reach the key
pocket -> `A` -> `USE`.

## 24. Three whiteouts, all avoidable, all harness-shaped **[mine + open]**

| where | what happened |
|---|---|
| Azalea Gym | policy fell back to slot 0 = TACKLE for a whole Scyther fight (Ember was slot 3) |
| Goldenrod Gym (Jigglypuff) | Sing + Disable; the engine spent 10 turns on "no action changed the battle state" retries |
| Goldenrod Gym (Whitney) | LEER picked as "best move" (#22), then 3 consecutive SUPER POTION uses that restored **0 HP** while Rollout ramped |

That last one is its own bug: `T12/T13/T14 me item:SUPER POTION ... me 9->9,
9->9, 9->0` with 7 Super Potions in the bag. The item flow reported a use,
consumed the turn, and healed nothing.

## 25. Whitney does not hand over the badge until the map reloads **[game, not a bug]**

Beating her sets `EVENT_MADE_WHITNEY_CRY` and
`SCENE_GOLDENRODGYM_WHITNEY_STOPS_CRYING` (maps/GoldenrodGym.asm:395+). I
talked to her six times and got nothing. You must **leave the gym and come
back**, then talk to her -> PLAIN BADGE + TM45. Cost: ~10 wasted calls
before I read the script.

## 26. Goldenrod Gym traps `goto` in a 2-tile pocket **[open]**

After the Whitney fight, standing at (8,4): `no path from (8,4) to (3,16)`,
`travel` failed the leg, and manual probes showed only (8,4) and (9,4)
walkable — `U`, `D`, `L`, `R` all "blocked" while `tiles_in` said (8,5) was
floor. `d.step_hold('D')` moved where `step_dir('D')` refused. The gym's
maze tiles need held steps, and nav's grid disagrees with the live engine.

## 27. Route 32 is gated on the Togepi Egg, and I walked to New Bark for
nothing **[mine]**

`Route32CooltrainerMStopsYouScene` (maps/Route32.asm:87) pushes you back to
Violet until `EVENT_GOT_TOGEPI_EGG_FROM_ELMS_AIDE`. I assumed the aide was
in Elm's lab and walked Violet -> Cherrygrove -> New Bark and back (two
full route legs, ~15 calls) before grepping the event and finding it is set
in **VioletPokecenter1F.asm:30** — the aide is in the *Violet Pokécenter*,
one screen from where I started. "The repo is the map" (gotcha 10) — grep
the event *first*.

## 28. `teach_hm` silently picked my carry and ate its best move **[mine]**

`d.teach_hm('H1','CUT')` returned `None` and taught **PANIC**, replacing
TACKLE with CUT. It teaches "the first ABLE mon" — with a Bellsprout on the
bench that was meant to be the mule. Use `teach_tm(tag, nickname,
forget=...)`, never the un-targeted variant.

## 29. `catch` names nothing when the species is new to the Pokédex **[open]**

`hunt(...)` + `d.catch(nickname='CACHE')` on a fresh GEODUDE: the catch
succeeded, but the **new-species Pokédex page** opens before the nickname
prompt, the battle module logged `frozen screen (state unchanged)` and
B-pressed out of it — which declined the nickname. Party ended up with a
plain `GEODUDE`. Same for the Togepi hatch (`Give a nickname to TOGEPI?`
auto-declined mid-walk) and the first BELLSPROUT.

Net effect on the persona: **only the starter is named.** The Goldenrod
Name Rater was the fallback plan and it did not work either — answering YES
to "Would you like me to rate names?" returned to the overworld without
ever opening the party list.

## 30. My own shop helper bought the wrong row **[mine]**

`shop((1,3), [('POKé BALL',10), ('POTION',3)])` bought 10 balls and then
**3 more balls** instead of Potions: after a purchase the list re-renders
and my cursor-row scan matched stale text. Verified from the bag, which is
the only trustworthy source (#13's lesson, re-learned).

## 31. `step_dir` reported a bogus failure reason with a traceback in it **[open]**

`d.last_step_reason == 'warp-cell(14,36): IndexError: list index out of
range'` on ROUTE_32 (18,8). The step actually worked. An exception string
leaking into a user-facing reason field means some warp-cell lookup is
indexing off the end of a row.

## 32. `menu: wait_for: predicate never true in N frames` is constant noise **[open]**

It appears 2-6 times in **every** battle in this session (hundreds of
lines). Either the predicate is wrong or the wait is redundant; as written
it hides the lines that matter.

---

# Part 3 — fixing the blockers, then hitting a wall in Ecruteak

I went back and fixed the P0s from Part 2 instead of grinding around them.
Five landed and are verified live; then the Ecruteak leg exposed a new
class of map the harness cannot walk.

## Fixes landed (all live-verified)

| # | fix | proof |
|---|---|---|
| 20 | `observe()['party']` now carries **`egg`** (+ schema field), and `train`'s heal rail **refuses to loop** when every non-egg member is already full | `[('PANIC',31,False),…]`; the 30-trip loop cannot recur |
| 20b | `train` will not rotate in a mon with **no damaging move**, and says which ones it skipped | new `RuntimeError` naming the blocked mons |
| 21 | a policy that **raises** is now logged (`[fight] policy RAISED TypeError: …`) instead of looking like a decline | — |
| 21b | `_auto_action` **always** re-resolves the slot through `best_move()`, so the fallback can never be a status move in slot 0 | — |
| 18 | the stall guard **never substitutes an attack for a ball** — a failed capture changes no vitals by design, which is why it kept KO'ing the mon I was catching | **caught a GROWLITHE L13 on the first try** after the fix; 4 attempts before it |
| 23 | `bag_item_index` grew a **key-item pocket** (`wNumKeyItems`/`wKeyItems`, flat id array, stride 1) and `use_item` now auto-detects the pocket and switches to it | SQUIRTBOTTLE is addressable instead of `not-in-bag` |

Unit lane after all of it: **593 passed**, same 2 pre-existing
`test_live_feed` failures.

## 33. Exp math makes the bench unsalvageable where I was standing **[mine]**

Even with `train` fixed: 16 more battles with GROWLITHE switched in and it
stayed **L13**. Route 35's wilds are L10-14 and exp is SPLIT with the
L32 carry, so a switch-in earns ~15 exp a fight against ~200 needed.
`HANDBOOK.md` says this outright ("the trainee must be the SOLE
participant and land the KO itself") — and `party_swap(0, 4)` (the only
way to make it the lead) **fails**: `cursor never reached first row 0`.
So the documented recipe is not executable through this build.

## 34. Ecruteak Gym: 31 interior warp tiles, and `goto` ping-pongs on the door **[open]**

`ECRUTEAK_GYM` reports **33 warps**, 31 of them interior floor holes that
all return `warp_id 3`. Every `goto` inside it ended as
`TravelError: map seam ECRUTEAK_CITY -> ECRUTEAK_GYM crossed 3x in one
call -- ping-pong cycle`, because the planner walks onto the exit door.
Nav has no concept of "this floor tile is a trap", so the only way through
is a hand-rolled BFS that treats every warp cell as a wall — which I wrote
(`holes = {…}`, floor-only BFS) and which then reports *no path*, because
the intended route genuinely goes **through** specific pads.

## 35. Burned Tower B1F slides/teleports on ordinary steps **[open]**

`step_dir('U')` from (10,9) landed on **(10,4)**; `step_hold('U')` from
(10,9) landed on (10,2); a plain `D` from (10,8) landed on (10,11). Five
cells of travel from one step. Whatever those tiles are (pit/slide), the
grid classifies them `floor`/`warp` and every path I planned desynced
after the first move. Add a real tile kind for them, or the floor is
un-navigable.

## 36. After a battle, every direction reads `blocked` for a while **[open]**

At B1F (10,11): nine consecutive `step_dir` calls all returned `blocked`
with `wScriptMode == 0` and no textbox; `close_menus()` + `settle()` then
made the very next `U` succeed. So `step_dir` needs its own settle after a
battle — otherwise a caller reasonably concludes it is walled in (I did,
twice, and hand-mapped a maze that wasn't there).

## 37. Morty is absent until the Burned Tower is finished **[game]**

`MORTY, the GYM LEADER, is absent.` — the gym is a dead end until the
beast scene + the rival battle at BURNED_TOWER_1F **(11,9)**
(`coord_event`, maps/BurnedTower1F.asm:298). The only real hole down is
**(10,9)**; every other B1F warp in that file is commented
`; inaccessible, left over from G/S` — I wasted several calls trying
(5,14) and (4,14) before reading it.

## 38. Lost the rival fight to Hypnosis + a broken item flow **[mine + open]**

`d.last_battle.summary()`: Flame Wheel did **0 damage on three
consecutive turns** (asleep), then three `SUPER POTION` uses restored
`1->1`, `1->1`, `1->18`. Same item bug as #24. I had no AWAKENING because
the Ecruteak Mart list needs **scrolling** and my first shop helper bought
a POKé BALL instead (#30 again). Bought 5 AWAKENINGs with a scrolling
version and added a sleep branch to the policy — but by then the tower
navigation had wedged.

---

# Part 4 — unblocked: badges 4 and 5

The blockers in Part 3 were mostly **my misreadings**, not map bugs. Two
retractions and the real explanations:

## 35. RETRACTED — Burned Tower B1F does NOT slide

I claimed a single step moved the player up to 5 cells. It does not.
**`step_hold(mv)` holds the direction through the whole transition**, and on
open floor a held key keeps walking: (10,9) -> (10,4) was five ordinary
steps of held UP. `step_dir` (a 4-frame tap) moves exactly one cell, every
time, verified: `(9,9) -> (10,9)`.

**Rule:** `step_hold` is ONLY for entering a warp/door tile where a wall
stops the overshoot. Everywhere else use taps (`press("U:6 .:26")` or
`step_dir`). This single mistake produced most of Part 3's "unnavigable
map" conclusions.

## 34. RETRACTED/EXPLAINED — Ecruteak Gym is pits, and it is solvable

`d.find_tiles('warp')` on ECRUTEAK_GYM returns only the two door tiles;
the 31 interior cells are tile kind **`pit`**. My earlier "31 interior
warps" came from `exits()` (which lists warp *events*), not from tile
kinds. A floor-only BFS **does** find a path to Morty — I walked it:
`(4,14) -> (4,13) -> (5,13) -> (6,13) -> (6,12..9) -> (5,9) -> (3,9) ->
(3,8) -> (3,7) -> (4,7) -> (6,7) -> (6,6) -> (6,5) -> (5,5) -> (5,4) ->
(5,3) -> (5,2)`, falling twice on the way (a fall costs nothing but the
walk back), which the fall-aware walker handled by re-planning.

## 39. The beast scene needs a TAPPED step onto (10,6) **[game]**

`SCENE_BURNEDTOWERB1F_RELEASE_THE_BEASTS` is scene 0, so the
`coord_event 10, 6` (maps/BurnedTowerB1F.asm:248) is live from the start —
but a coord_event only fires on the step that ENTERS the cell. My earlier
attempt arrived at (10,6) mid-hold and skipped it. One tap per cell up the
(10,9) -> (10,6) corridor fired it immediately.

Also: `BurnedTowerB1FLadderCallback` turns the exit ladder into plain floor
until the beasts are released, so **B1F is a one-way trip** if you fall in
early. After release, `(7,15)` works — entered from `(6,15)` with a held
step (a wall at (8,15) stops the overshoot).

## 40. Chuck's gym: I parked a boulder in the only gap **[mine]**

Cianwood Gym needs STRENGTH (HM04, OLIVINE_CAFE (4,3)) and the boulders
must be pushed in the right ORDER. Row 5 has exactly two gaps: x4 and x5;
x5 is permanently occupied by the defeated Blackbelt Lung. I pushed the
(4,7) boulder straight up into (4,4) — the last free gap — with (4,3) a
wall behind it, and soft-locked the puzzle.

**Recovery:** boulder positions reset when you leave and re-enter the map.

**Working order** (Strength activated after every map entry):
1. from (3,8) push U: boulder (3,7) -> (3,6)
2. from (5,8) push U: boulder (5,7) -> (5,6)
3. from (5,7) push L: boulder (4,7) -> (3,7)   <- column 4 now clear
4. walk up (4,7) -> (4,4), then (3,4) -> (3,3) -> (3,2) -> (4,2), face UP.

Note a boulder push does NOT move the player on the same press; the first
press only turns you. Press twice per push.

## 41. Blind A presses in the Cianwood pharmacy bought 6 Potions **[mine]**

Gotcha 13, verbatim, and I still did it: the pharmacist is a SHOP, and
`SECRETPOTION` is only offered after meeting Jasmine at the lighthouse. My
"advance the dialog" loop pressed A into an open shop list and bought
6 POTIONs at ¥300. Cheap this time (Chuck's payout left ¥19.5k) but it is
the same mistake as #30 and #13.

## 42. The Olivine Lighthouse stairs will not fire from any side **[open]**

1F `(16,13)/(17,13)` and 2F `(16,11)/(17,11)` are listed as warps to the
floor above, and stepping onto them — tapped or held, from every walkable
neighbour — never changes the map. Only 1F `(3,11)` and 2F `(5,3)` work,
and they lead to 3F's LEFT column, which is a dead end (rows 12-13 are
walled off from the x12-17 region that holds the 4F stairs). Either those
tiles are one-way down-landings whose real up-route I have not found, or
the warp table and the collision data disagree. This is what stopped
Jasmine.

## 43. Fly is in the bag with nobody to fly it **[open, mine]**

HM02 FLY was collected from Chuck's wife (CIANWOOD_CITY (10,46) — she
walks, so she may be at (11,46); read her dialog page by page, the give is
on the 11th page). **No party member can learn it** — Fly needs a bird and
I have none, so every trip is still on foot/surf. Same failure mode as
`PROGRESS.md`'s original FLY lesson, one step removed.

---

# Part 5 — the cheap win, and the lighthouse SOLVED

## 42. RETRACTED — the Olivine Lighthouse stairs are fine; they are PITS

I claimed the lighthouse "up-warps never fire from any side". Wrong on both
counts. Reading the **collision byte** under each warp event settles it
(`constants/collision_constants.asm`):

| byte | name | behaviour |
|---|---|---|
| `$72` | `COLL_LADDER` | a real climbable warp |
| `$70` | `COLL_WARP_CARPET_DOWN` | door/carpet (works) |
| `$60` | **`COLL_PIT`** | you FALL through, one way, downward |
| `$00` | plain floor | warp EVENT exists, but no warp collision -> never fires |

Every lighthouse warp cell, by floor:

```
1F (3,11)=72 up      (16,13)/(17,13)=00   <- landing pads, dead tiles
2F (5,3)=72 up       (16,11)/(17,11)=00   (16,13)/(17,13)=60 pit down
3F (13,3)=72 (9,5)=72 (16,9)/(17,9)=00    (16,11)/(17,11)=60 pit down
4F (3,5)=72 (9,7)=72 (16,7)/(17,7)=00     (16,9)/(17,9)+(8,3)/(9,3)=60 pit
5F (9,15)=72         (16,5)/(17,5)=00     (16,7)/(17,7)=60 pit down
6F                                        (16,5)/(17,5)=60 pit down
```

So the tiles I hammered were `$00` **arrival pads**. The lighthouse is
"climb the left/middle LADDERS, descend the right-hand PIT chain", and one
deliberate pit-fall is required on the way UP.

My other error was even dumber: **I printed the 3F grid only to row 13 and
concluded the floor's three vertical strips were disconnected.** Rows
**14-15** are a full-width corridor (x4-x15) joining all of them.

### The route, verified by single-stepping every cell

UP: 1F (3,11)L -> 2F (5,3)L -> 3F: (4,3) -> (3,4) -> x2/x3 down to row 13
(the trainer at (3,9) blocks x3, walk x2) -> (4,14) -> east row 14 to
(14,14) -> up x14 to (14,5) -> (13,4) -> **(13,3)L** -> 4F: (13,2) -> west
row 2 to (12,2) [an NPC at (11,2) blocks row 2 further west] -> (12,3) ->
(11,3) -> (10,3) -> **(9,3) PIT** -> 3F strip B (9,3) -> (9,5)**L** ->
4F (9,5) -> (9,7)**L** -> 5F -> down x9 to (9,15)**L** -> 6F -> up x9 to
(9,9) -> Jasmine at (8,8).

DOWN: 6F row 8 east to x17 -> up to the (17,5) pit, then just tap D twice
per floor: 5F(17,7) -> 4F(17,9) -> 3F(17,11) -> 2F(17,13) -> 1F. Four falls.

## 44. `press()` silently stops delivering input after some warps **[open]**

On the 2F ladder tile every `d.press("L:6 .:26")` reported no movement for
six calls, `wScriptMode == 0`, no textbox, `observe()['tiles']` saying the
neighbour was floor. Raw `pyboy.button_press('left')` moved immediately.
Whatever swallows those presses (an internal freeze flag or a hook), the
**escape hatch is raw pyboy button presses**, and that is what I used for
the entire lighthouse. Related to #7 but not caused by me this time.

Also confirmed again: after a pit fall / ladder / battle, `wScriptMode`
stays 1 for a while and EVERY direction reads `blocked`. Always
`scene()`/settle first, then step.

## 45. The PC deposited my carry **[mine]**

To free a party slot for the bird I opened the Pokécenter PC (collision
byte `$93` = `COLL_PC`; `find_tiles` has no kind for it, so locate it with
`[(x,y) for ... if grid[y][x]==0x93]` -> `(9,1)`, stand at `(9,2)` facing
UP). Then I mashed A through the deposit flow: it stored BELLSPROUT, went
straight back into the party list, and **stored PANIC — the L44
Typhlosion**. Withdrew it immediately, then had to fix the party order by
hand (START -> POKéMON -> PANIC -> SWITCH -> TOGEPI), because
`party_swap(0,4)` still fails with `tmhm_pocket: START menu never opened`.

Never blind-press inside the PC. One press per screen, read every page.

## 46. Catching needs a FREE SLOT and a ball that exists **[mine]**

Two wasted hunts: 20 encounters throwing at NOCTOWL with a full party
(caught mons go to the box, so the harness's "is it in the party?" check
can never succeed), then 22 more with **zero Poké Balls in the bag**
(`policy action ('ball','POKE BALL') impossible (not in bag)`). Check
`observe()['bag']` and the party size BEFORE hunting. Great Balls from the
Olivine mart caught the L16 Noctowl at full HP on the second throw.

## 47. My shop helper still can't move the cursor between purchases **[open]**

Buying GREAT BALL x10 then trying SUPER POTION bought **14 Great Balls**:
after a purchase the list re-renders and the ` ▶` scan matches the old row.
Third occurrence (#13, #30). Verify from the bag every time.

---

# Part 6 — toward Pryce: Lake of Rage done, parked inside the Rocket base

## 48. Pryce's gym is HARD-GATED by the Rocket hideout **[game]**

The fisher at MAHOGANY_TOWN **(6,14)** stands directly on the only approach
to the gym door at (6,13) and cannot be moved or talked past
(`object_event 6, 14, ... EVENT_MAHOGANY_TOWN_POKEFAN_M_BLOCKS_GYM`,
maps/MahoganyTown.asm:269). That event is set by
**`RocketBaseElectrodeScript`** (maps/TeamRocketBaseB2F.asm), the script
that also hands over **HM06 WHIRLPOOL** and sets
`EVENT_CLEARED_ROCKET_HIDEOUT`. So: no hideout, no Pryce. There is no
shortcut, and `goto` reporting `blocked-by-stationary-npc` on that cell is
the correct answer, not a bug.

## 49. FLY is outdoor-only, and I burned six calls forgetting it **[mine]**

`fly()` failed three times in a row from inside OLIVINE_GYM: `select_label`
found `▶FLY`, the confirm press did nothing, and the party menu stayed up.
It is `PROGRESS.md`'s own pt12 note (FLY is outdoor-only and a failed
indoor attempt leaves the menu open). Step outside first.

Working fly recipe: outdoors, `START -> POKéMON`, then move the party
cursor by READING it (`▶` row) rather than pressing D a fixed number of
times — the party menu REMEMBERS its cursor, so blind presses wrap around
and select the wrong mon. Then A on `▶FLY`, cycle the town list with D
(`screen_text()[1]` is the name), A, A.

## 50. The Rocket base is a three-floor connectivity maze; here is the map

What I derived (collision bytes, `L` = `$72` ladder):

```
B1F  entrance ladder (27,2) <-> MAHOGANY_MART_1F (7,3)
     row 2 runs x1..x26 (security cameras at (24,2),(6,2) -> grunt fights)
     rows 5-16 the left corridor is x3..x5 (NOT x1: (1,5) is wall)
     22 exploding traps fill x1-5, y7-13 -> Voltorb fights, harmless at L48
     ladder to B2F at (3,14)
B2F  arrival (3,14). Bottom block rows 13-16; row 12 is solid wall x1..x23,
     so the bottom block CANNOT reach B2F's left ladders (3,2)/(3,6).
     Row 16 is the through-corridor (x7..x28) -> up column 27 to the
     (27,14) ladder -> B3F. (23,14)/(22,15) are $90 WALL, not the door.
B3F  arrival (27,14). Bottom-right -> row 13 crosses x15 (rows 12-13 are
     the only east-west link in the middle), row 16 runs x1..x14,
     column 16/17 is the only way from row 12 up to rows 8-10,
     row 10 is split at x15 ($90) and row 8 likewise, so B3F's WEST half
     (x1..x14, which holds the rival coord_event at (8,10) and the boss
     coord_events at (10,8)/(11,8)) is reachable only from B2F's LEFT
     ladders (3,2)/(3,6) -- i.e. the base must be entered a second time
     along a different branch.
```

That last line is the thing to know before the next attempt: **B2F's
bottom block and B2F's left block are separate**, and the story content on
B3F lives behind the left one. B1F's internal warps ((5,15)->B1F#4,
(25,2)->B1F#3) are almost certainly the branch that reaches it.

Progress banked before I stopped: Lake of Rage cleared (Red Gyarados
beaten -> **RED SCALE**), Lance recruited, B1F cleared (grunt + traps),
B2F crossed, B3F partially explored (PROTEIN + ULTRA BALL picked up),
PANIC now **L48**. Save: `claude-rocket-base-b3f.state`.

---

# Part 7 — I asked whether the map view is accurate. It was ACCURATE and INCOMPLETE.

## 51. The verdict: the grid never lied. The picture hid half the map. **[fixed]**

Two different questions, two different answers.

**Is the decoded map DATA accurate?** Yes, byte-exact. `nav.grid()` decodes
`maps/<Name>.blk` + `data/tilesets/*_collision.asm` statically, so I built
the same grid from the LIVE block map in WRAM (`wOverworldMapBlocks`: a
3-block border on every side, `stride = wMapWidth + 6`, map block (bx,by)
at `(by+3)*stride + bx+3`) and diffed the two:

| audit | result |
|---|---|
| all 53 `saves/claude-*.state` | **0 mismatched cells** |
| BURNED_TOWER_1F entered live (beasts released) | 0 |
| SPROUT_TOWER_3F, ROUTE_32, TEAM_ROCKET_BASE_B3F | 0 |

The only cells that CAN disagree are `changeblock` cells, and
`nav.conditional()` already enumerates them ahead of time. On
BURNED_TOWER_1F that is exactly `(7,15)` and `(10,9)` -- the basement
ladder and the hole (`BurnedTower1FHoleAndLadderCallback`, maps/
BurnedTower1F.asm:27-35). Verified the detector fires by poking the
Giovanni-door block live: drift reported `(10,9) 0x07->0x00  (11,9)
0x07->0x00`, precisely the two cells `conditional()` predicted.

**Is `map_view()` accurate?** Every glyph it drew was right. But it is a
**reachability** render, not a map render, and everything unreachable drew
as a SPACE -- indistinguishable from wall or off-map. So a whole walkable
wing renders as void, and `_GLYPH_LEGEND` had `(" ", "unreachable from
here")` while the renderer filtered that very line out (`if g != " "`).
The one glyph that needed explaining was the one never explained.

That is what burned Part 6. Rocket base B3F looked like a small
L-shaped floor; I spent ~25 calls dumping raw collision hex to discover a
57-cell western wing holding the rival and boss `coord_event`s. The
picture had that region, as blank.

### The fix

- Walkable cells of a component the player cannot reach now draw `,`, and
  their warps `o`. Cells in the player's OWN component are untouched
  (water without SURF stays blank -- drawing it would claim reachability).
- New `offregion:` annotation line per unreachable component: cell count,
  bounding box, and **how to get in** -- the warps that open onto it, or
  the `changeblock` that does when no warp touches it. Door lists are
  capped at 4 (Ecruteak Gym has 26 pads into one region).
  Door-less components under 8 cells are drawn but not annotated.
- `Driver.live_grid()`, `Driver.grid_drift()`, `Driver.sync_grid()`:
  read the engine's own map, list the disagreements, and push them into
  nav as live overrides so PATHING gets them too. `map_view()` prints a
  `DRIFT:` line whenever the live map disagrees, so the picture can never
  quietly be a version behind the engine.

B3F now answers the question that cost a session, in one line:

```
offregion: 57 walkable cells at x 1-14, y 1-10 -- NOT reachable from here;
           enter via (3,2)->TEAM_ROCKET_BASE_B2F  (3,6)->TEAM_ROCKET_BASE_B2F
offregion: 46 walkable cells at x 7-14, y 1-8 -- NOT reachable from here;
           enter via changeblock at (10, 9)  changeblock at (11, 9)
```

and `sync_grid()` after that changeblock fires merges the two (3 components
-> 2), so `goto` will route into the boss room instead of refusing.

## 52. `screen --png` is the visual ground truth and I never used it **[mine]**

`./crystal screen` decodes the tilemap to TEXT, which is garbage for
terrain (gotcha 11) -- and I let that put me off the screen entirely for a
whole run. `./crystal --state S screen --png /tmp/x.png` writes the real
framebuffer, and reading that PNG shows the room the way a player sees it.
On B3F it immediately showed the Poké Ball at (17,2) that `observe()`
listed as an NPC. Use it whenever the question is "what am I looking at".

## 53. The integration lane cannot run in this checkout **[open]**

`tests/integration/conftest.py:23` forks `claude_saves/wren-*.state`, and
there is no `claude_saves/` here -- 16 tests ERROR on FileNotFoundError
before touching any code. Pre-existing and environmental (those are
another run's milestones), but it means map/nav changes can only be
verified against `saves/claude-*.state` by hand, which is what I did: 53
states swept, 0 render errors, 0 drift.

---

# Part 8 — the "2 pre-existing failures" were a missing 12-line hook

## 54. I reported a red suite as background noise for the whole run **[fixed]**

Every test line in this file up to Part 7 says "**593/625 passed**, same 2
pre-existing `test_live_feed` failures". I never opened them. They said:

```
AttributeError: 'Crystal' object has no attribute 'observe'
```

`crystalagent/live.py` -- a complete, tested frame publisher -- wants an
emulator that slices its own `tick()` so a viewer can be handed ~20 fps out
of an emulator running thousands. `crystalagent/emu.py` had:

```python
def tick(self, frames=1):
    self.py.tick(frames, False)
```

No observer, ever. The whole watch pipeline was dead on arrival, and #1
recorded that as "the docs describe a superset of this checkout" and forked
a script around it. Wrong diagnosis: the publisher was written, the
*attachment* was missing, and it was 12 lines:

- `Crystal.observe(obs)` + a sliced `tick()` (chunks of `obs.slice_frames`,
  `obs.due()` decides whether the chunk renders, `after_slice(n, rendered)`
  reports what PyBoy actually did, never overshoot the request).
- `Driver(state, live={...})`, `live_attach()`, `live_detach()` -- both
  AGENTS.md and HANDBOOK.md already documented these.
- `paths.LIVE_DIR`, which `feed_paths()` referenced and nobody had defined.

Two bugs surfaced the moment it ran:
- a narration handler still attached at interpreter shutdown writes to
  closed streams: `Error in sys.excepthook` x3 after a clean leg.
  `live_attach` now registers an atexit detach.
- `detach()` left the viewer on whatever frame the fps throttle last owed --
  end a leg after a menu and the viewer stares at that menu forever.
  `detach()` now publishes a final frame first.

**Now: 627 passed, 0 failed** -- the suite is green for the first time in
this run -- and `scripts/newgame_bedroom.py` drives a fresh power-on
through the title, Oak's speech, the clock and the naming keyboard while
publishing every frame, including the keyboard, which is never savestated.
`scripts/newgame_claude.py` is deleted; the documented script works.

**Lesson:** a failing test you did not read is not "pre-existing noise", it
is an unread bug report. Two lines of traceback would have bought a
watchable run six sessions earlier.

---

# Part 9 — hideout cleared, badge 7, and the Ice Path (nav's worst map)

## 55. I probed movement for six calls with a wild GOLBAT on screen **[mine]**

Trying to work out why a step failed, I tapped directions and recorded
"blocked" for all four — twice — and built a whole theory on it. A battle
intro was eating the input the entire time; `observe()['ui']` had said
`textbox: False` one call earlier, before the encounter started, and my
tap helper never re-checked. The screen said `Wild GOLBAT appeared!`.

**Rule:** a movement probe MUST refuse to run while `d.battle()` or
`ui['textbox']`. My `rawtap` now returns `('BATTLE', ...)` instead of
pressing, and every conclusion drawn from a page of fake "blocked"
readings had to be thrown away.

## 56. `_enterable` treated every ICE tile as a wall **[fixed]**

`MapData._enterable` listed WALKABLE / WARPS / HOPS / (WATER when
surfing) — and **not ICE** — while `slide()` right below it models ice
perfectly and `find_path`'s `expand` handles it. Consequences:

- `_reach` (so `map_view` and every `offregion:` line) could not enter ice
  at all: ICE_PATH_1F reported **81 reachable cells and "582 walkable
  cells NOT reachable"**, and Mahogany Gym rendered as four rows.
- After adding ICE: the same cell reaches **254** cells and the B1F
  stairs appear in the `warps:` line.

## 57. A side wall will not start a slide **[fixed]**

ICE_PATH_1F (28,10) is `$b2` COLL_UP_WALL with ice at (28,9). Stepping U
onto it from the floor below works; stepping U *off* it onto the ice never
moves — four primitives (`rawtap`, `step_hold`, `step_dir`, `press`), on a
battle-free screen, `wTilePermissions` reading `$04`. `goto` plans that
step, the engine refuses, the cell is marked live-blocked, and 20 replans
later it bails with `replan-storm`.

But Union Cave 1F's corridors **do** cross `$b2` rows upward onto plain
floor (trek.py's own note, and a unit test that asserts it). So the
refusal is specific to STARTING A SLIDE off a side-wall tile, not to
leaving it. My first fix banned the direction outright and broke that
test; the narrowed rule (`_SIDE_WALL_NO_SLIDE`, applied only when the
destination is ICE) keeps both behaviours. 627 tests pass.

## 58. `goto`'s executor mis-drives ice; its PLANNER is fine **[open]**

On ICE_PATH_1F (15,2), `nav.find_path` returns a 36-step plan beginning
`L`. `goto` reports `blocked R at (15, 2)` twenty times and bails. Same
cell, same grid, opposite direction. I stopped debugging the executor and
wrote a 12-line driver that follows `find_path` one step at a time,
re-planning after each move — it crossed the entire maze in ONE call.

**Recipe until this is fixed:** plan with `nav.find_path`, execute
yourself, re-plan every step, and retry a stuck step once with a longer
press (24 frames) before believing it.

## 59. Immovable boulders are STOPPERS the grid does not model **[open]**

ICE_PATH_B2F_MAHOGANY_SIDE is a solid ice rectangle: every slide dumps you
back to B1F, and the (9,11) stairs sit in a floor pocket no slide can
stop in. The four boulders on that floor ("It's immovably imbedded in
ice") are the stoppers that make it solvable — and they are `object_event`s
gated on `EVENT_BOULDER_IN_ICE_PATH_nA`, so they only exist once you have
pushed the matching B1F boulder into its hole.

`nav.grid()` knows none of it, and `observe()['npcs']` is a LOCAL view
(sprites near the player), so a boulder two screens away is invisible to
planning. Modelling one fallen boulder as a wall
(`nav.set_cell(map, 4, 7, 0x07)`) turned "no path" into a 5-step path.

## 60. STRENGTH switches off when you change maps **[mine]**

I activated STRENGTH on ICE_PATH_B1F, went up to 1F, out to Route 44, came
back — and the first push silently did nothing (`push b3 R did not move
me`). Re-activating fixed it instantly. Activate it on the floor where you
are pushing, every time you re-enter.

Also: the push helper reports failure when the boulder falls into a hole,
because the player does NOT step forward on that push. `The boulder fell
through.` in the textbox is the success signal.

## 61. The Ice Path solution, written down

B1F boulders pair with holes by `stonetable` (macro: `warp id, object id`)
— boulder N -> the Nth `def_warp_events` entry:

| boulder | at | hole | B2F stopper appears at |
|---|---|---|---|
| 1 | (11,7) | (11,2) | (11,3) |
| 2 | (7,8)  | (4,7)  | (4,7) |
| 3 | (8,9)  | (5,12) | (3,12) |
| 4 | (17,7) | (12,13)| (12,13) |

Pushing **one** boulder is enough. The cheapest is boulder 2, and the
sequence (all verified live) is:

1. b3 blocks the corridor east: push it R once, then D three times ->
   (9,12), which also opens the (9,10)-(9,11) link south.
2. b2: push L twice -> (5,8); push U twice -> (5,6).
3. b1 blocks the (11,7) link the long way round: push it U twice -> (11,5)
   (row 3 then bypasses it).
4. Walk the long way (south -> row 16 east -> east column -> row 13 ->
   x12 column -> (11,6) -> row 5 -> row 3 -> row 1 -> the NW chamber) to
   (6,6); push b2 L -> (4,6); stand (4,5) and push D -> it falls.
5. Fall through B1F (5,12) yourself -> B2F (4,12); with the stopper at
   (4,7) modelled, (9,10) is 5 steps; then (9,11) -> B3F.
6. B3F (15,5) -> B2F_BLACKTHORN (3,15) -> B1F south (5,25) -> 1F (36,27)
   -> **BLACKTHORN CITY**.

## 62. Clair is gated on the RADIO TOWER, not on the Ice Path **[game]**

The gym guard says "CLAIR entered the DRAGON'S DEN behind the GYM" —
`maps/BlackthornCity.asm:38,66` check `EVENT_CLEARED_RADIO_TOWER`. So
badge 8 needs Goldenrod's Radio Tower cleared first. Route 44's east end
is walled (verified by walking: (57,10) has three blocked cells east), so
the Ice Path really is the only road east — the `ROUTE_44 -> BLACKTHORN_CITY`
edge in `attributes.asm` is a map band, not a path.

## 63. Goldenrod's underground shutters are invisible to nav **[known]**

`nav.grid`'s docstring already says macro-generated changeblocks with
symbolic coords (exactly the Goldenrod underground doors) are not scanned.
Live effect: the warehouse doors (22,10)/(23,10) sit in a 12-cell region
that `offregion:` reports as "NOT reachable ... no warp and no changeblock
found" from every other region on the map. The switches are `bg_event`s at
(16,1), (10,1), (2,1) plus an emergency switch at (20,11), and the door
layout comes from `wUndergroundSwitchPositions` (01:d963).
**Flip a switch, then `sync_grid()`** — that is what the drift reader is
for.
