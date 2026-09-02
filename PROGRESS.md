# PROGRESS — run RUSTY

Newest section first. Persona contract: `persona_RUSTY.md` (binding).
## 2026-09-01 - session rusty-1 (cont.): CHAMPION

**Position:** NEW_BARK_TOWN (13, 6), frame 18890005. Party: [('TORCH', 60, 202, 202), ('SPROCKET', 47, 127, 127), ('CHAIN', 48, 160, 160), ('SPARK', 48, 130, 130)]. EVENT_BEAT_ELITE_FOUR set; Hall of Fame + credits done,
game continued from the post-game SRAM save. Checkpoint `saves/rusty-champion.state`.
- E4 attempt 3 (after training): TORCH L57-60 solo'd Will/Koga/Bruno/Karen (learned FLAMETHROWER at 60 mid-Karen). First Lance try wiped
  (TORCH lead: Gyarados Surf + Hyper Beam). Reloaded the post-Karen save and won with a matchup policy (kernel `lance_policy`): SPARK (Magneton,
  Steel/Electric) leads and beats Gyarados (Thundershock 4x), both L47 Dragonites (Thunder/Twister/Blizzard/Hyper Beam all resisted) and
  Aerodactyl; SPROCKET Rock Throw for the L50 Dragonite (2 hits) and CHAIN Waterfall for Charizard. 7 Hyper Potions + 1 Full Restore.
- Lance's room: nav refuses the (4,5)/(5,5) coord_event cells (the scene never advances) -- goto (5,6) then step U by hand.
- Persona ledger: RUSTY broke bullet 1 (grinding, user-approved) and goal 6; every other bullet held. Team cap five never exceeded.

## 2026-09-01 - session rusty-1 (cont.): user lifted the no-grind rule; team trained to 47-57

**Position:** ROUTE_26_HEAL_HOUSE, frame 18209479. Party: TORCH Typhlosion L57, CHAIN Gyarados L48, SPROCKET Graveler L47 (EARTHQUAKE at 41),
SPARK Magneton L47. Money ¥11336. Checkpoint `saves/rusty-trained.state`.
- `d.train` (grass-only, Route 26 L28-32 wilds) gave ~1 level per 30 battles: too slow. Kernel `grind()` on Victory Road's south room
  (box x2-12,y60-66 from the (9,65) entrance; wilds L32-40) with `d.pace` + trainee-lead/switch-to-TORCH policy did ~1 level per 8 battles;
  ~350 battles total, ~4 min emulation each 100. Heal rail = Route 26 heal house (`travel` through the gate; Indigo PC is unreachable
  from that room -- travel's own Victory Road route dies at the (0,35) ledge).
- Fixed in trek.py `_battle_text_handler`: a forget menu whose every row is an HM now DECLINES (CHAIN at L40 HYDRO PUMP looped 5 x 90k frames).
- Next: restock at Indigo, E4 attempt 3.

## 2026-09-01 - session rusty-1 (cont.): E4 attempt 2 (within persona) wiped at Bruno

**Position:** INDIGO_PLATEAU_POKECENTER_1F, frame 10274881. Party: [('TORCH', 50, 166, 166), ('CHAIN', 38, 125, 125), ('SPROCKET', 33, 88, 88), ('SPARK', 27, 60, 60)]. Money ¥8893.
Checkpoints: `saves/rusty-pre-e4.state` (Indigo PC, ¥11336, 6 Hyper/2 Full Restore/5 Revive bought, TORCH still has SWIFT+EMBER),
`saves/rusty-e4-wipe2.state` (now, 3 Hyper Potions, ¥8893).
- TMs are single-use and `teach_tm` mis-navigates the TM pocket (cursor remembered on the HM rows; row read lags the press). Hand-driven teaching
  (kernel `teach_slow`) forgot the WRONG moves twice (party-list cursor glyph matched before the move list's): TORCH lost SWIFT and DIG, ended with
  CUT / FLAME WHEEL / IRON TAIL / ROLLOUT. TM23, TM28, TM04 consumed.
- Attempt 2: Will took 32 turns (Slowbro walled CUT-only damage, 1 Full Restore + revives), Koga burned the rest (Muk/Crobat), Bruno's Machamp
  wiped what was left. Root cause unchanged: TORCH L50 is the only mon that scales; CHAIN L38 / SPROCKET L33 / SPARK L27 die to one hit at L42-47.
- Conclusion: the League is not reachable under the persona's no-grind rule with this party. Options: grind Victory Road wilds (persona break),
  or accept 8 badges as the run's result.

## 2026-09-01 - session rusty-1 (cont.): League reached; E4 attempt 1 wiped at Karen

**Position:** INDIGO_PLATEAU_POKECENTER_1F, frame 10254359. Party: [('TORCH', 51, 170, 170), ('CHAIN', 38, 125, 125), ('SPROCKET', 33, 88, 88), ('SPARK', 27, 60, 60)]. Money ¥10618 (whiteouts halve it; was ¥75k before Clair).
Checkpoints: `saves/rusty-pre-e4.state` (Indigo PC, ¥30k, before shopping), `saves/rusty-indigo.state`, `saves/rusty-e4-wipe1.state` (now).
- Route 45/46/29/27/26 swept with CHAIN leading (L32 -> L38). Tohjo Falls: WATERFALL up at (9,12) facing U, then east pool (20,5) step D rides the
  east falls down; exit is (25,15) entered by stepping D. Route 27 east of the falls: nav's decoded grid is garbage (0x72 "ladder" bytes on the
  path) -- used the live-grid walker (`lbfs`/`lwalk` in the kernel, treats 0x72 as floor, mounts SURF via `d._mount_surf`). Route 26 heal house
  (15,57): teacher at (2,3) heals free. Victory Road: live-grid walker with hop-aware BFS (ledge landings are NOT collision-checked by the engine),
  ladders (13,31)->(13,17)->(13,5). Rival beaten in Victory Road (Sneasel/Magneton/Golbat/Haunter/Feraligatr...).
- Harness fixes this session: battle items target wCurBattleMon (battle.py play()), tactics.recommend 'threatened' heal now only when ONE max hit
  reaches me and a potion changes it (it burned 6 Hyper Potions at 105+/170 into Machamp). Learn policy declines when all four moves are HMs
  (CHAIN: WATERFALL/WHIRLPOOL/STRENGTH/SURF loops the forget menu otherwise). A Geodude got caught by the re-armed encounter policy (owned check
  by species) -> boxed at Indigo; `H.KO_SPECIES` now lists every plan species.
- E4 attempt: Will (TORCH solo), Koga (TORCH solo), Bruno (TORCH + CHAIN, 2 revives) -- then Karen's HOUNDOOM L47 (Crunch 72-85, Karen Max
  Potions it) outspeeds and kills CHAIN/TORCH; wiped with Houndoom at 24 HP. Lance (44-47 x5) is beyond this party (TORCH 51, CHAIN 38,
  SPROCKET 33, SPARK 27) without levels; persona forbids grinding and every route trainer is spent. DECISION NEEDED (see chat).

## 2026-09-01 - session rusty-1 (cont.): RISING badge (8/8)

**Position:** DRAGON_SHRINE (5,3), frame 8517232. Party: [('TORCH', 47, 156, 156), ('CHAIN', 32, 105, 105), ('SPROCKET', 33, 88, 88), ('SPARK', 26, 58, 58)]. Money ¥15605. Badges 8/8.
Checkpoints: `saves/rusty-rising.state` (shrine, badge in hand), `saves/rusty-clair-beaten.state` (Blackthorn PC), `saves/rusty-pre-clair.state`.
- Clair wiped us TWICE (whiteouts halved the wallet: ¥75k -> ¥9k). Causes: (1) `play()` used every battle item on party slot 0 (the lead), not the
  active mon -- Hyper Potions for CHAIN landed on a full-HP benched TORCH. Fixed in `crystalagent/battle.py`: `('item', NAME[, slot])`, default
  slot = wCurBattleMon. (2) `tactics.recommend(heal_at=0.4)` told a full-HP CHAIN to use HYPER POTION every turn vs Kingdra (bug, unexplored).
  (3) Clair's AI SWITCHES: a low Dragonair goes out for Kingdra; Kingdra quarters fire.
  Won on attempt 3 with a hand policy: TORCH Flame Wheel the Dragonairs (potion < 50 HP), switch to CHAIN vs Kingdra, 2x X ATTACK then STRENGTH,
  REVIVE by explicit slot. Persona: "two wipes -> TM/catch" satisfied by X ATTACK + TM-less item play (no grinding).
- Blackthorn Gym 2F boulders: stonetable wants SPECIFIC boulders in specific holes (boulder(8,2)->(8,3), (2,3)->(2,5), (6,16)->(8,7)); decoys must be
  shuffled first. 1F: (7,7) is a hole-landing warp tile nav refuses -- walk (7,8)->U->R by hand to reach Clair's region via the dropped bridges.
- Dragon's Den B1F: static nav grid is WRONG (IndexError in _is_water_cell); used `live_grid()` (0x29 water, 0x27 buoy, 0x24 whirlpool at (10,20)).
  Whirlpool needed: taught HM06 to CHAIN (over BITE), `use_field_move('WHIRLPOOL', facing='D')` at (10,19), surf to (13,30), walk to (19,30), U = shrine.
  Quiz: cursor defaults to option 1; Q3 needs DOWN x1 (Tough person), Q5 needs DOWN x1 (Both).
- Next: exit den (Clair gives TM24 at B1F (19,30) scene), HM07 WATERFALL at ICE_PATH_1F (31,7), then Route 45/46 -> New Bark -> Route 27 -> League.

## 2026-09-01 - session rusty-1 (cont.): Ice Path crossed, Blackthorn

**Position:** BLACKTHORN_POKECENTER_1F, frame 8140977. Party: [('TORCH', 44, 146, 146), ('SPARK', 26, 58, 58), ('SPROCKET', 33, 88, 88), ('CHAIN', 31, 101, 101)]. Badges 7/8.
Checkpoint `saves/rusty-blackthorn.state`. Also `saves/rusty-radiotower.state` (Goldenrod, after Archer).
- Radio Tower cleared (Archer beaten, Clear Bell). Route 44 swept.
- Ice Path: 1F rink needs a slide BFS (`nav.slide` over the decoded grid; goal (16,8) from the (15,2) rink entry). B1F boulder puzzle solved with a
  boulder-push BFS (stand cell + reachability, pits = 'pit' tiles); boulders reset on map re-entry, STRENGTH must be re-activated per entry (face boulder, A, YES),
  goto WILL push boulders it walks into -- walk with a boulder-aware BFS instead. B2F Mahogany side: boulders land at (11,3),(4,7),(3,12),(12,13) (map_objects),
  then slide to the (8,8) island and take (9,11) ladder. B2F Blackthorn side: decoded grid is wrong; used `live_grid()` + own BFS (0x23 ice, 0xa3 ledge).
- HM07 WATERFALL at ICE_PATH_1F (31,7) NOT collected yet ("no approach") -- needed for Tohjo Falls. Next: Clair, then HM07.

## 2026-09-01 — session rusty-1 (cont.): GLACIER badge, CHAIN caught

**Position:** MAHOGANY_TOWN, frame 7089742. Party: TORCH (Quilava) L33 CUT/QUICK ATTACK/FLAME WHEEL/EMBER; SPARK L25 THUNDER WAVE/THUNDERSHOCK/SUPERSONIC/SONICBOOM; SPROCKET (Graveler) L33 HARDEN/DYNAMICPUNCH/ROCK THROW/MAGNITUDE; SPROCKET#2 (stray Geodude L21, to be boxed); CHAIN (red Gyarados) L30 THRASH/BITE/STRENGTH/SURF. Boxed at Mahogany: PLIERS (Gyarados L27), a Magikarp, LUG (Olivine). Badges 7/8.
**Done:** Route 42/43 trainers, red GYARADOS caught first Ultra Ball (Thunder Wave + 2 Thundershock), Lance, Rocket hideout (Petrel, Ariana, three Electrodes; passwords from the two tail grunts; boss door at B3F (10,9) reached via B2F north corridor -> B3F (3,2)), HM06, Pryce (TORCH solo, Flame Wheel).
**Next:** Elm's call -> Goldenrod Radio Tower (Rockets), Rising Badge at Blackthorn via Route 44/Ice Path (fork `rusty-pre-clair`), then Route 27/26/Victory Road, E4.
**Persona:** party cap honored at 5 real mons (the stray Geodude counts; boxing it next PC visit). RIVET (Gastly) still uncaught -- night-only; skipping unless a night falls on the route (goal list does not require it).

### Gotchas
- Rocket base B1F statue cameras are coord_events nav seals; `_refresh_nav_blocks` recomputes every goto, so a session-level unblock set (see rusty_helpers) is needed to walk them (each fires a 2-grunt ambush once).
- B1F wild Voltorb/Geodude/Koffing cannot be fled (`'flee' changed nothing`); the harness substitutes attacks, fine.
- After `sync_grid()` patches a changeblock door, `route()` fails with "no routable mapgraph path" from inside the opened region (region ids shift); `nav.clear_overrides(map)` fixes it.
- The generator room seals behind you (Lance's coord_event at (12,10)/(12,11)) until all three Electrodes are gone: heal BEFORE Ariana.

## 2026-09-01 — session rusty-1 (cont.): MINERAL badge

**Position:** OLIVINE_CITY, frame 5924578. Party: SPROCKET L32, TORCH L29, SPARK L16, PLIERS L27. Badges 6/8 (ZEPHYR HIVE PLAIN FOG STORM MINERAL). HM02 FLY in the bag (nobody planned can learn it).
**Jasmine:** two wipes first (Magnemite THUNDERBOLT is x4 on Gyarados, 136-160; Magnitude does 5-6 to Steelix; Thundershock does 0). Third try: SPROCKET leads (Electric-immune) and Magnitudes both Magnemites, switches to PLIERS the turn Steelix appears, PLIERS Surfs through two Hyper Potions, PLIERS switches back to SPROCKET when Magnemite #2 comes out. `outlook()`/`tactics.explain` gave every number needed -- read it BEFORE the first attempt next time.
**Next:** Ecruteak -> Route 42 -> Mahogany (fork `rusty-pre-pryce` later), Lake of Rage red GYARADOS (CHAIN, spend every Great Ball), Rocket hideout, Pryce.

## 2026-09-01 — session rusty-1 (cont.): STORM badge

**Position:** CIANWOOD_CITY, frame 5640787. Party: PLIERS (Gyarados, fished as MAGIKARP L20 at Olivine, evolved L21) L26 STRENGTH/TACKLE/SURF/DRAGON RAGE; TORCH L29; SPARK (Magnemite) L16; SPROCKET (Graveler) L31. LUG boxed at Olivine. Badges 5/8. Bag has SECRETPOTION, RARE CANDY, HM01-05, 9 GREAT BALL, 5 REPEL.
**Done:** Route 38/39 trainers, SPARK caught (Route 38 grass), Good Rod, lighthouse to Jasmine (route: 4F (9,2) step D into the (9,3) hole -> 3F center column -> (9,5) stairs -> 4F (9,7) stairs -> 5F center room -> (9,15)), Kuni beaten by PLIERS -> HM03 SURF, HM04 from the Olivine cafe sailor, Route 40/41 swimmers, Chuck (Dragon Rage x3 on Poliwrath). Cianwood gym boulders: push (5,7) up, (3,7) up, then (4,7) RIGHT from (3,7); column 4 is then open (Lung at (5,5) never moves, so the middle boulder must never go north).
**Next:** FLY to Olivine (teach HM02 to PLIERS? Gyarados cannot learn Fly -- nobody planned can; walk/surf), SECRETPOTION to Amphy, Jasmine (fork `rusty-pre-jasmine`), then Route 42 -> Mahogany, Lake of Rage (CHAIN), Rocket hideout.

### Harness fixes
- `Battle._party_row_select`/`_drive_forced_switch` read `wMenuCursorY-1` (the party list is a 2D menu; `scroll_abs` added a stale pack scroll offset -> index 4..8 on a 4-mon party, infinite select loop). Forced-switch drives are now bounded (6) and bail 'wedged'.
- `_edge_steps` accepts WATER approach cells when `nav.surf` is on (Route 40->41 was "no routable path").

## 2026-09-01 — session rusty-1 (cont.): FOG badge + harness fixes

**Position:** ROUTE_37 south end, frame 4521204. Party: SPROCKET (Graveler) L27 HARDEN/SELFDESTRUCT/ROCK THROW/MAGNITUDE; TORCH (Quilava) L28; LUG (Togepi) L5. Badges: ZEPHYR HIVE PLAIN FOG. 5 REPEL, 2 ESCAPE ROPE, SQUIRTBOTTLE, BICYCLE. ~4400 yen.
**Done:** Sudowoodo (SPROCKET self-destructed on it -- SELFDESTRUCT/EXPLOSION are now filtered out of every policy pick), Route 37, Bill scene in the Ecruteak PC, rival 3 in Burned Tower (won), beasts released, four Kimono girls beaten, Morty's gym (SPROCKET Magnitude), FOG badge.
**Losses:** whiteout vs Kimono Kuni's VAPOREON (Water Gun 72 on SPROCKET; TORCH cannot dent it). HM03 SURF still with the Surf guy at DANCE_THEATER (7,10) -- needs Kuni beaten.
**Persona re-plan:** MAREEP does not exist in Crystal's wild tables (`data/wild/johto_grass.asm` has no MAREEP) -> SPARK = MAGNEMITE (Route 38/39). Nobody planned can learn SURF before CHAIN; the Good Rod at Olivine will supply a Water mon named from the fallback list (PLIERS), benched once CHAIN arrives. LUG is dead weight and gets boxed when a slot is needed.
**Next:** Route 38/39 trainers, catch MAGNEMITE (SPARK), Olivine (Good Rod, lighthouse Amphy), Kuni -> SURF, Cianwood (Chuck; fork `rusty-pre-chuck`), Secret Potion -> Jasmine.

### Harness fixes this session (crystalagent/battle.py, trek.py)
- The "choice loop when switching": the SHIFT battle style's "will you change POKeMON?" YES/NO got answered YES by a blind text press, then the loop A-mashed "Which PKMN?" -> SWITCH -> "already out"/"An EGG can't battle!" until the frame cap. `_text_speed_byte` now forces BATTLE_SHIFT (SET style), `play()` backs out of a party list it did not open (`_party_list_up`), and `_drive_forced_switch` skips EGG slots.
- `switch_to` reported success on a SWITCH whose A was swallowed (box just drawn); `_submenu_select` is now confirm-until-closed, and the party list gets a settle before the row confirm (a remembered cursor made the first A fire during list setup). Verified two consecutive switches on Route 37.
- `rusty_helpers.py` holds the session's policies/helpers; a kernel restart is `from rusty_helpers import *; d = boot('rusty')`.

## 2026-09-01 — session rusty-1 (cont.): PLAIN badge

**Position:** GOLDENROD_CITY outside the gym, frame 4207301. Party: SPROCKET (Geodude) L21 TACKLE/SELFDESTRUCT/ROCK THROW/MAGNITUDE; TORCH (Quilava) L26 CUT/LEER/QUICK ATTACK/EMBER; LUG (Togepi) L5. Badges: ZEPHYR HIVE PLAIN. Bag: BICYCLE, TM04/28/31/45/49, HM01/05, balls x6, misc. ~5200 yen (halved twice by whiteouts).
**Done:** rival 2 at Azalea gate (won), Ilex Farfetch'd herded (facing table from the script), HM01 CUT on TORCH, Ilex/Route 34 trainers+items, Goldenrod bicycle (free), gym trainers, Route 35 / National Park / Route 36 trainers with SPROCKET LEADING (lead-and-switch or solo when safe) -> L10->L20, TM04 Rollout + TM28 Dig picked up, egg hatched (named LUG via the Goldenrod Name Rater after the hatch prompt confirmed blank), Whitney beaten by SPROCKET alone: 2x X ATTACK, Rock Throw x2 Clefairy, Magnitude x2 + Rock Throw Miltank. Goal 3 met (no potion, no Fury Cutter).
**Losses:** whiteout on Route 35 (walked on with TORCH fainted -- sweep now heals at the PC when the lead <45% or anyone is down); whiteout at Whitney attempt 1 (trainee policy switched TORCH into a ramped Rollout: 71-84 dmg).
**Next:** flower shop SQUIRTBOTTLE, dept store (REPEL x5, GREAT BALL x5 -- persona spend), Route 36 Sudowoodo -> Route 37 -> Ecruteak (Morty; fork `rusty-pre-morty`). Catch GASTLY (RIVET) in Sprout... no: Gastly lives in Ecruteak's Burned Tower/Tin Tower -- catch there on the way. MAREEP on Route 42/43.

### Gotchas
- `d.cut()` failed with `no-prompt` in Ilex: the tree prompt is "This tree can be CUT!" then a YES/NO — press A facing it, `flush_dialog` -> `resolve_choice('YES')`, then `sync_grid()`. On Route 35 (17,6) `d.cut()` worked directly.
- The egg hatch naming prompt arrives as a YES/NO ("Give a nickname to TOGEPI?") during a goto; `_take_pending_nickname` sees the hatchling still flagged `egg` so the species match must not filter eggs.
- Name Rater party menu rows are 1/3/5 (row 0 blank); the cursor REMEMBERS the last position, so read the ▶ row before pressing DOWN (renamed TORCH to LUG by accident once, fixed the same visit).
- `talk_to` on a gym leader can return 'talked' with the battle only starting after; check `d.battle()` and call `fight()`.

## 2026-09-01 — session rusty-1 (cont.): HIVE badge

**Position:** AZALEA_TOWN outside the gym, frame 3650431. Party: TORCH (Quilava) L22 TACKLE/LEER/EMBER/QUICK ATTACK; EGG (Togepi); SPROCKET (Geodude) L8. Bag: SUPER POTION, X ATTACK, AWAKENING, POKé BALL x4, GREAT BALL x2, TM31, TM49, HM05. ~4000 yen. Badges: ZEPHYR, HIVE.
**Done:** Route 32 (Albert, Liz, Roland, Gordon, Peter; fishers on the pier skipped as off-path), Union Cave 1F all five trainers + items, SPROCKET caught at (13,16) UNION_CAVE_1F with a POKé BALL on the walk; Route 33 Anthony; Kurt; Slowpoke Well (4 grunts + Proton); Azalea Gym (Benny, Al, twins; Josh unreachable) and Bugsy (Ember sweep).
**Persona clarification:** goal 2 "Hive before Union Cave" is geographically impossible (Azalea is only reachable through the cave); honored as "pass straight through, no exploring" -- only 1F on the direct line was swept.
**Loss:** one whiteout on Route 33 (Hiker Anthony) walking in with TORCH 20/56 and 0 EMBER PP after Union Cave -- cost ~2000 yen + the walk back from Violet. Lesson: check lead HP/PP before the next trainer.
**Next:** rival ambush leaving Azalea west (fork `rusty-pre-rival2`), Ilex Forest Farfetch'd -> HM01 CUT (teach TORCH), Route 34 -> Goldenrod. MAREEP (SPARK) lives on Route 32/42/43 -- catch on Route 42 later.

### Gotchas
- `tactics.recommend` can return `('switch', idx)` pointing at the EGG slot; the party menu then wedges ("An EGG can't battle!") and burns 5x90000 frames per fight. `rusty_policy` now refuses switches to eggs/fainted mons.
- `_resolve_nickname(callable, species)` is called for EVERY wild (fled ones too) -- a callable that consumes a fallback list drains it on Rattatas. Keep the callable pure; only planned species get names.

## 2026-09-01 — session rusty-1 (cont.): ZEPHYR badge

**Position:** VIOLET_CITY outside the gym, frame 256265. Party: TORCH (Quilava) L15. Bag: POTION x2, POKé BALL x5, TM31, HM05. Badges: ZEPHYR.
**Done:** Route 30 trainers (Mikey, Joey [number accepted], Don), Sprout Tower all sages + Sage Li (HM05), rival scene in tower, escape-roped out, Falkner beaten with TORCH alone (Ember 2HKO Pidgey, 1HKO Pidgeotto). Goal 1 met: no other party member exists.
**Skipped:** Bug Catcher Wade (Route 31) — off the walked path.
**Next:** Elm's aide phone call/egg is later; Route 32 south → Union Cave → Route 33 → Azalea; catch GEODUDE (SPROCKET) in Union Cave on the way (planned slot); Slowpoke Well, Bugsy (fork `rusty-pre-bugsy`).

### Checkpoints (cumulative)
| file | where |
|---|---|
| rusty-torch, rusty-cherrygrove, rusty-pre-rival1, rusty-egg-delivered, rusty-violet, rusty-pre-falkner | see names |
| `saves/rusty-zephyr.state` | VIOLET_CITY, 1 badge |

### Gotchas
- First rival fight (L5 vs L5 Totodile) is LOST by straight Tackle spam (two whiteouts). Leer x2 first then Tackle wins cleanly. `outlook()['enemy']` carries stats only — species name is in `battle_frame()['enemy']['species']`.
- `tactics.recommend` puts the sacrifice line BEFORE healing, so a persona "heal when lethal" rule must be checked before calling it (rusty_policy does).
- Sprout Tower and Violet Gym trainers all fire on sight-line during the approach; `talk_to` then reports False/"nothing answered" although the fight happened — read `encounter_events`, not the return value.


## 2026-09-01 — session rusty-1: fresh game → Cherrygrove

**Owner:** session rusty-1, working state `saves/rusty.state`.
**Persona:** RUSTY / Cyndaquil "TORCH" / five-mon cap / no `d.train` / fork only before gyms+rival.
**Position:** `CHERRYGROVE_CITY` (39,6), frame 24348. Party: TORCH (Cyndaquil) L5 20/20, TACKLE/LEER. Bag: POTION x1. ¥3000. Badges 0.
**Done:** new game typed as RUSTY; mom's clock chain (SUNDAY / DST / 10:00 AM / phone) answered YES; Elm's errand accepted; Cyndaquil taken at ELMS_LAB (6,3) and named TORCH on the gift keyboard; aide's POTION collected; Route 29 crossed with three wilds fled (SENTRET x2, PIDGEY — none on the plan).
**Next objective:** Route 30 → Mr. Pokémon's house (egg + Oak's Pokédex) → back to Cherrygrove → rival fight at New Bark (fork `saves/rusty-pre-rival1.state` first, persona rule) → deliver egg to Elm → Route 30/31 trainers → Violet City → Falkner with TORCH alone (goal 1).

### Persona wiring (re-arm each session)

```python
PLAN = {'GEODUDE':'SPROCKET','MAREEP':'SPARK','GASTLY':'RIVET'}   # + red GYARADOS → CHAIN
d.encounter_policy = <'catch' iff species in PLAN, not owned, party < 5; else 'flee'>
d._pending_nickname = <table name> before any gift scene (Togepi → LUG, Eevee → AXLE)
```

### Checkpoints

| file | where | notes |
|---|---|---|
| `saves/rusty-torch.state` | ELMS_LAB (5,3) | TORCH just received, lab scene finished, aide potion NOT yet taken |
| `saves/rusty-cherrygrove.state` | CHERRYGROVE_CITY (39,6) | first town; guide NPC not yet met |

### Gotchas found this session

- Elm's starter script opens the mon PICTURE window with `ui.textbox=False` and the YES/NO box is drawn only after a further A; `talk_to(6,3)` returns `'talked'` there. `flush_dialog` → `resolve_choice('YES')` loop (three YES boxes: "you want CYNDAQUIL?", "fire POKéMON?", "nickname?") then the keyboard consumes `_pending_nickname`. Worked first try.
- `d.save()` refuses while `wScriptMode=1` — after a cutscene, `flush_dialog` until `'done'` and `settle()` before saving.
