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
