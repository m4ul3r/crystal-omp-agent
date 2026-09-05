# PROGRESS — run TALLOW

Newest section first. Persona contract: `persona_TALLOW.md` (binding).
Previous run (RUSTY, Champion) is archived below the TALLOW sections.

## 2026-09-04 - session tallow-1: CHAMPION -- Hall of Fame at 46:49:07

**Final in-game time: 46:49:07** (`wGameTimeHours`, frozen at the Hall of Fame save; frame 12,331,780).
**Position:** NEW_BARK_TOWN (13,6) after the credits, `saves/tallow-champion.state`. EVENT_BEAT_ELITE_FOUR set.
Party: EMBER Typhlosion L64, CRUST Graveler L48, CRUMB Raticate L47, FLOUR Pidgeot L48, SUGAR Togetic L47,
BRINE Poliwhirl L46. Money ¥34,864.
- HM07 WATERFALL: Ice Path 1F rink, stand (30,7) facing R (slide sequence from (21,9): R,U,R,L,D,R). The HM region
  is one-way (flows down to Blackthorn); leave via the west ice to Route 44. Blackthorn's Route 44 edge is NOT
  reachable from town for `goto` (UP_WALL row 28 / ledge geometry) -- `fly()` in tallow_lib (party menu FLY +
  fly-map name matching) is the way out of Blackthorn.
- Dragon's Den whirlpool regenerates on every map reload (no event flag): re-WHIRLPOOL from (10,21) facing U on
  the way back. Den B1F landing has an NPC at (20,5); `live_walk` (tallow_lib) routes on the live block map with
  NPC cells, learned walls, one-way hop ledges and warps-as-walls (a warp on the path fired the ladder mid-walk).
- Tohjo Falls: (9,12) face U WATERFALL -> (9,7); east pool (20,5) step D rides down; exit (25,15) -> Route 27 (36,6).
  Victory Road: ladders (1,49)->(1,35) pocket -> (13,31)->(13,17) -> (13,5); `live_walk` with hop landings
  un-collision-checked (RUSTY's note) finds it. A stray background run burned 1.35M frames on Route 27 (a wedged
  trainer intro) -- replayed from `tallow-tohjo.state` to keep the clock honest (saved ~6 game hours).
- Harness fix: `_consult_learn_policy` mon/move regexes no longer require "<MON> ... TO LEARN" on one frame
  (mid-battle 2-line box) -- CRUST had silently traded EARTHQUAKE for EXPLOSION under auto. `tallow_lib.boot`
  also installs a learn policy that DECLINES SELFDESTRUCT/EXPLOSION.
- Training: 400 wild battles at Victory Road top floor (box around (13,7), heal at Indigo PC) took the four
  L38-40 mons to 46-48 (`tallow_grind.py`); EMBER as anchor went 52 -> 60 (ceiling broken, see ledger).
- E4 (`scripts/tallow_e4.py`, matchup tables per member, revive+heal_party between rooms): Will/Koga/Bruno/Karen
  are EMBER Flamethrower with CRUST for Muk, BRINE for Onix/Houndoom, CRUMB Super Fang for Umbreon. Lance wiped
  three forks (tactics policy healed too late; CRUST self-destructed; L47 fodder vs Hyper Beam). Won with
  `lance_policy`: EMBER solo, HYPER POTION under 115 HP, FULL RESTORE on status, everyone else only ever a
  REVIVE-turn shield. 19 turns, 8 Hyper Potions + 2 Full Restores, no faints.
- Persona ledger (honest): goal 6 met. Deviations: EMBER over the ace+3 ceiling (64 vs 53) from anchoring the
  grind; bought 22 Hyper Potions / 3 Full Restores / 6 Revives at Indigo (the "no potions where there is a
  Pokécenter" rule read as a town rule, not a gauntlet rule); two whiteouts on the WORKING state at Lance
  (money ¥124k -> ¥31k) before the fork discipline was re-applied.

## 2026-09-03 - session tallow-1: RISING badge (8/8) -- all Johto badges

**Position:** DRAGONS_DEN_B1F (19,30), just outside the shrine. Party: EMBER Typhlosion L48, CRUST Graveler L47,
CRUMB Raticate L39, SUGAR Togetic L38, FLOUR Pidgeot L40, BRINE Poliwhirl L39 (SURF/WHIRLPOOL). Badges 8/8.
Bag adds CLEAR BELL. Money ~¥100k.
Checkpoints: `tallow-pre-clair.state` (Blackthorn PC, gym trainers + bridges done), `tallow-pre-den.state`,
`tallow-shrine.state` (forced save, quiz open), `tallow-rising.state` (milestone).
- Route 45/46 is downhill-only: from west Johto, Blackthorn is reached ONLY via Mahogany + Ice Path
  (`scripts/tallow_to_blackthorn.py`; boulders/pits persist, so the second crossing is slide -> stairs ->
  fall into (11,2) or (5,12) -> slide to the (9,11) ladder). Keep `nav.surf` on except for Route 32 (its river
  makes goto pop a SURF prompt it cannot answer).
- Blackthorn Gym: only two bridges are needed -- 2F (8,2)->(8,3) [after pushing the (6,1) decoy east to (9,1)]
  and (6,16)->(8,7) [after pushing the (8,14) decoy south to (8,17)]; the NW quadrant ((2,3)->(2,5)) sits behind
  the cooltrainer at (4,1) and was never entered. `tallow_boulders.py` takes an assignment JSON with arbitrary
  goal cells plus an ignore list; it now treats stairs, NPCs and learned-blocked cells as walls, advances
  trainer text before waiting for the battle, and settles after fights. Clair's region: 2F (7,9) stairs down,
  `sync_grid()`, goto (5,4); `nav.clear_overrides` before `travel` out.
- Clair: EMBER Flame Wheel on the Dragonairs, CRUMB Hyper Fang on Kingdra (matchup_policy), 13 turns, EMBER
  finished at 2 HP, CRUMB fainted. No whiteout, first try.
- Dragon's Den: B1F NPC at (20,5) blocks the landing -- route around it; whirlpool at (10,20) from (10,19)
  facing D (`use_field_move`), then live-grid walk to (19,30), U into the shrine. The elder's quiz starts on
  entry: answers option 1,1,2,1,2 (Q3 "Tough person", Q5 "Both"); badge on leaving the shrine.
- Persona ledger: goal 5 met (eight badges, every leader from a `tallow-pre-*` fork, whiteouts only on forks);
  ceilings broken along the way (EMBER +5, CRUMB earlier); no items bought beyond balls/repels; no sales.

Next: Elm's Master Ball call, Route 27/26 -> Victory Road -> Indigo Plateau (goal 6: Elite Four with the
original six; TM/HM moves via TM23 IRON TAIL / TM16 already held). Kanto is out of scope for the persona.

## 2026-09-03 - session tallow-1: Radio Tower cleared (EVENT_CLEARED_RADIO_TOWER)

**Position:** RADIO_TOWER_5F (16,5). Party: CRUST Graveler L45 (EARTHQUAKE), EMBER Typhlosion L47, CRUMB L39,
SUGAR L38, FLOUR L40, BRINE L39. Bag adds BASEMENT KEY, CARD KEY, TM16.
Checkpoints: `tallow-goldenrod2.state`, `tallow-radio-5f.state`, `tallow-radio-key.state`,
`tallow-underground.state`, `tallow-warehouse.state`, `tallow-cardkey.state`, `tallow-radio-4f.state`,
`tallow-pre-archer.state`, `tallow-radio-cleared.state`.
- `scripts/tallow_radio.py` stages 0-2. 5F fake director: the coord cell (0,3) fired only when stepped on with
  `step_dir` (goto arrived without triggering). The Goldenrod (11,29) underground stairs are sealed by the
  takeover grunt at (16,23) ((16,22) is a wall): use the north stairs (9,5) -> switch room (20,27) -> (21,25)
  -> tunnel (2,2) -> basement door bg_event (18,6) from (18,7) (needs BASEMENT KEY) -> step U warps to (21,31)
  -> (22,27) -> switch corridor. Switches: pos = s1*1 + s2*2 + s3*3; each position only sets the doors it
  names, so order matters: all three on (pos 6) then switch 1 off/on (pos 5 -> 6) leaves 3,5,6,8,9,11 open
  = corridor to the warehouse door (22,10) via (22,11). Director resets everything to 0 on the way back:
  the emergency switch (20,11) (face U from (20,12)) reopens the way out. 3F card key slot: face U from (14,3).
  4F (12,0) stairs to 5F took three held entries. Archer: coord (16,5) from (16,6).

Next: walk back to Blackthorn (Route 34 -> Ilex -> ... -> Route 45), Clair.

## 2026-09-03 - session tallow-1: Ice Path crossed; Blackthorn Gym locked behind the Radio Tower

**Position:** BLACKTHORN_CITY (18,13). Party: CRUST Graveler L39, EMBER Typhlosion L44, CRUMB Raticate L39,
SUGAR Togetic L38 (SHADOW BALL via TM30), FLOUR Pidgeot L40, BRINE Poliwhirl L39. Money ¥62526. Repels spent.
Checkpoints: `tallow-pre-icepath.state`, `tallow-icepath-b1f.state`, `tallow-icepath-pits.state`,
`tallow-icepath-b2f.state`, `tallow-blackthorn.state`, `tallow-pre-clair.state` (Blackthorn PC).
- Route 44 grind to the Clair floor (38): `tallow_grind.py`, 268 + 37 battles; EMBER 44 (ceiling 43, +1).
- Ice Path: 1F rink `slide_to(d, (16,8))` from (15,2) (`tallow_lib.slide_to`, live grid + NPC/boulder walls);
  B1F boulders: `scripts/tallow_boulders.py` (per-boulder push BFS, feasible order, single-tile pushes = 28-44
  frame holds; boulder positions from `d.sprites()` movement 25 -- `map_objects` keeps dropped ones); pits at
  (4,7)(5,12)(11,2)(12,13). B2F Mahogany side: the four dropped boulders are `map_objects` (not sprites) --
  pass them as `avoid`; slide to (8,8)->(7,8)->... the (9,11) ladder; B3F travel() works; B2F Blackthorn side
  slide (3,10)->(3,14) drops you straight down the (3,15) ladder to B1F (11,27); then travel to Blackthorn.
- `d.goto` PUSHES boulders it walks into (it moved (11,7) to (11,8)); walk boulder floors with the script's own BFS.
- Blackthorn Gym door is blocked by the super nerd (18,12) until EVENT_CLEARED_RADIO_TOWER: Goldenrod Radio
  Tower + Underground first (walk: Route 45/46/29/.../34 -- no fly helper).

Next: Goldenrod Radio Tower (Rockets), Underground (Basement Key -> Card Key), Archer -> back to Blackthorn,
2F boulders (RUSTY: (8,2)->(8,3), (2,3)->(2,5), (6,16)->(8,7); decoys first), Clair (fork `tallow-pre-clair`).

## 2026-09-03 - session tallow-1: GLACIER badge (7/8), hideout cleared

**Position:** MAHOGANY_GYM (5,4). Party: SUGAR Togetic L29, CRUMB Raticate L38, EMBER Typhlosion L37, BRINE Poliwag
L30, CRUST Graveler L37, FLOUR Pidgeotto L34. Money ~¥50k. Badges 7/8. Bag adds HM06, RED SCALE.
Checkpoints: `tallow-mahogany.state`, `tallow-lake.state`, `tallow-lance.state`, `tallow-hideout-{b1f,b2f,b3f,
petrel,ariana,done,out}.state`, `tallow-pre-pryce.state`, `tallow-glacier.state` (milestone).
- Lake of Rage: red Gyarados KO'd (not in the plan), Lance at (21,28). No Route 43 toll paid (travel took the
  grass side).
- Hideout (`scripts/tallow_hideout.py`, staged): `d.trip_scenes = True` walks the camera ambushes; B1F stairs
  (3,14); B2F south region only reaches B3F via (27,14); passwords need a SECOND talk to the two grunts
  (`endifjustbattled`); B3F boss door (10,9) is a bg_event -- face U from (10,10), press A, then `sync_grid()`;
  reaching it = B3F (27,2) -> B2F north -> B2F (3,2) -> B3F west. Ariana's door (14,12) likewise from B2F
  south (14,13). After any reload inside the base call `sync_grid()` (map callbacks reopen doors nav thinks are
  walls); `route()` out of B2F fails -- walk (3,14) -> B1F (27,2) -> mart by hand.
- Pryce: Mahogany Gym is an ice rink; `scripts/tallow_ice.py` BFS-es over slides (0x23 ice, 0x07 rock, NPCs)
  -- from the entrance side: UURDDLUR reaches (5,4). matchup SEEL->CRUMB, DEWGONG->CRUST, PILOSWINE->EMBER; 11 turns.
- `d.party_swap` intermittently fails ("SWITCH entry not found") right after a heal -- `set_lead` returns False;
  callers retry.

Next: Route 44 -> Ice Path (3 REPEL rule: have 2 -- buy 1 at Mahogany) -> Blackthorn -> Clair (fork
`tallow-pre-clair`; Kingdra L40 -> floor 38, ceiling 43). HM07 WATERFALL at ICE_PATH_1F (31,7) on the way.

## 2026-09-03 - session tallow-1: MINERAL badge (6/8)

**Position:** OLIVINE_GYM (5,4). Party: CRUST Graveler L32, BRINE Poliwag L28, FLOUR Pidgeotto L34 (WING ATTACK),
SUGAR Togetic L28, EMBER Quilava L35, CRUMB Raticate L36. Badges 6/8. Bag adds TM01, TM23.
Checkpoints: `tallow-amphy.state` (6F, Jasmine explained), `tallow-potion.state`, `tallow-pre-jasmine.state`,
`tallow-mineral.state` (milestone).
- Lighthouse: `travel` picks the one-way (16,13)/(17,13) landings on 1F and loops. Up = 1F(3,11) 2F(5,3) 3F(13,3)
  4F goto (9,2) + step D into the (9,3) hole -> 3F centre -> (9,5) -> 4F (9,7) -> 5F (9,15) -> 6F. Down = the
  east column 6F(16,5) 5F(16,7) 4F(16,9) 3F(16,11) 2F(16,13) 1F (`scripts/tallow_jasmine.py`).
- Jasmine: CRUST Magnitude on the Magnemites, BRINE Surf on Steelix (matchup_policy), 18 turns, CRUST fainted
  at the end, no whiteout.

Next: Route 42 -> Mahogany -> Route 43 -> Lake of Rage (red Gyarados = wild, KO it; Lance) -> Rocket hideout
(Lance + rival? no: Lance) -> Pryce (fork `tallow-pre-pryce`; Piloswine L31 -> floor 29). LADLE still boxed.

## 2026-09-03 - session tallow-1: STORM badge (5/8), HM02/03/04 in hand

**Position:** CIANWOOD_POKECENTER_1F. Party: FLOUR Pidgeotto L31 (FLY), EMBER Quilava L35 (CUT), CRUST Graveler
L30 (STRENGTH), SUGAR Togetic L28, BRINE Poliwag L28 (SURF), CRUMB Raticate L36. LADLE Magnemite L21 boxed at
Ecruteak. Money ¥30468. Badges 5/8. Bag: 2 REPEL, 2 POKé BALL, LURE BALL, SQUIRTBOTTLE, GOOD ROD, TM30/31/45/49,
HM01-04.
Checkpoints: `tallow-hm03.state`, `tallow-olivine.state`, `tallow-brine.state`, `tallow-cianwood.state`,
`tallow-pre-chuck.state`, `tallow-storm.state` (milestone).
- Kimono girls: EMBER lead + CRUMB anchor, one heal trip. HM03 from the gentleman (7,10).
- BRINE: GOOD ROD (Olivine house (13,15), guru (2,3)) at the Ecruteak pond: Poliwag 55% on the good rod
  (`data/wild/fish.asm` Pond_Good); `scripts/tallow_fish.py` casts with `use_item("GOOD ROD")` facing water.
  LADLE boxed to make room (persona: water slot beats a second electric/ground).
- `tactics_policy` now strikes SELFDESTRUCT/EXPLOSION: CRUST self-destructed 240 wild battles in a row (0 exp,
  anchor EMBER 28 -> 40) before I noticed; rolled back to `tallow-brine`.
- `boot()` calls `enable_surf()`; without it `route()` has no Route 40 -> 41 edge.
- Cianwood Gym: goto's boulder shoving pushed the middle boulder north into (4,4) -- (4,3) is a WALL, so the gym
  soft-locks (RUSTY's warning). Re-entering resets the boulders; `scripts/tallow_chuck.py` drives the recipe by
  hand (push (5,7) U, (3,7) U, (4,7) R from (3,7), column 4 up, then (3,4)->(3,2)->(4,2)). FLOUR (FLY) beat Chuck.
- Chuck's wife hands HM02 only after the STORM badge. Pharmacy sells until Jasmine has explained Amphy's illness.
- Persona ledger: EMBER 35 / CRUMB 36 vs Chuck ceiling 33; goal 4 (FLY before leaving Cianwood) met.

Next: Olivine lighthouse -> Jasmine (Amphy) -> Cianwood pharmacy SECRETPOTION -> lighthouse -> Olivine Gym
(Jasmine: Magnemite 30/30, Steelix 35; fork `tallow-pre-jasmine`; CRUST Magnitude / EMBER fire).

## 2026-09-03 - session tallow-1: FOG badge (4/8), six-slot core complete

**Position:** ECRUTEAK_GYM (5,2). Party: CRUMB Raticate L35, SUGAR Togepi L21, FLOUR Pidgeotto L31, CRUST Geodude
L21, EMBER Quilava L25, LADLE Magnemite L21 (THUNDER WAVE over TACKLE). Money ~¥17k. Badges 4/8. Bag: 2 REPEL,
4 POKé BALL, LURE BALL, SQUIRTBOTTLE, TM30/31/45/49, HM01.
Checkpoints: `tallow-r36.state`, `tallow-ecruteak.state`, `tallow-pre-rival3.state`, `tallow-burned.state`,
`tallow-pre-morty.state`, `tallow-fog.state` (milestone).
- Squirtbottle chain: meet Floria on Route 36 (33,12) FIRST, then Floria in the shop (wanders: `sprite_cell`),
  then the teacher (2,4). Route 35's CUT tree (17,6) regrows on every map load -> `tallow_lib.travel` now cuts
  the nearest tree when a leg reports "no path" and CUT is known.
- Sudowoodo: talk_to(35,9) -> YES -> the wild battle starts a few frames AFTER talk_to returns; poll `d.battle()`.
- Ecruteak PC: Bill's scene on first entry; drain before `heal()`. Burned Tower exit is the LADDER at B1F (7,15)
  (the (10,8)/(10,9) warps are one-way holes; travel picks them and loops); Eusine at (10,12) blocks the corridor
  until talked to. Rival 3 won with `matchup_policy` (HAUNTER->EMBER, CROCONAW->CRUMB, MAGNEMITE->CRUST).
- Route 38 grind (Tauros/Miltank base exp): `tallow_grind.py` now keeps the anchor in slot 2 (`set_lead(nick,
  second)`) and trains owned mons while the hook hunts; the earlier version let the anchor lead for 200 battles
  (CRUMB 21->37) and had to be rolled back to `tallow-burned`. Only 2 POKé BALLs were in the bag, so the hook's
  first Magnemite burned both -- restocked 5 at Ecruteak.
- Morty attempt 1 (CRUST lead, Magnitude) WHITED OUT at T34 -- reloaded `tallow-pre-morty`. Attempt 2: CRUMB lead,
  PURSUIT (dark) vs ghosts, immune to Shadow Ball/Night Shade: 7 turns, no faint.
- Persona ledger: goal 3 met (six slots by Ecruteak, nothing below 21 at Fog) but the water slot is LADLE
  (electric) -- BRINE (Poliwag) still needed for SURF; CRUMB 35 / FLOUR 31 vs ceiling 28 (grind anchor overshoot);
  one whiteout on a fork (undone).

Next: Kimono girls (HM03 SURF), Route 38/39 -> Olivine (Jasmine needs the lighthouse Secret Potion from Cianwood),
HM02 FLY from Chuck's wife in Cianwood (goal 4), Chuck (fork `tallow-pre-chuck`). BRINE: Poliwag on Route 30/31/44.

## 2026-09-03 - session tallow-1: PLAIN badge (3/8)

**Position:** GOLDENROD_POKECENTER_1F. Party: CRUST Geodude L21, FLOUR Pidgeotto L23, EMBER Quilava L23 (CUT over
LEER), CRUMB Rattata L18, SUGAR Togepi L5. Money ¥12145. Badges ZEPHYR HIVE PLAIN. Bag: 2 REPEL, 2 POKé BALL,
LURE BALL, TM31/45/49, HM01.
Checkpoints: `tallow-pre-rival2.state`, `tallow-ilex.state`, `tallow-hm01.state`, `tallow-r34.state`,
`tallow-pre-whitney.state`, `tallow-plain.state` (milestone).
- Rival 2 (Ilex gate): EMBER lead, won at 6/65 (harness auto-policy; a trainee policy got CRUST+FLOUR KO'd on the
  first try -- rolled back to the fork).
- Farfetch'd: `scripts/tallow_ilex.py` `BIRD` table = position -> (cell, player facings that ADVANCE it), read off
  `maps/IlexForest.asm` (`ifequal <facing>` branches are the BACKWARD ones). `wFarfetchdPosition` reads 0 before the
  first talk (treat as 1). `talk_to(cell, facing=f)` reports a non-standable spot for some facings; try the next.
  PIDGEY cannot learn CUT (learnset) -> EMBER carries it (forgot LEER).
- SENTRET is not on Route 34 (only 29/43); normal slot = CRUMB (Rattata, persona table). WANT now also MAGNEMITE
  (Routes 38/39) -> LADLE and POLIWAG -> BRINE.
- Whitney: CRUST lead, Miltank down in 7 turns but CRUST and FLOUR fainted; badge on the second talk after leaving
  the gym (gotcha 29). A Lass's Jigglypuff DISABLE wedged a fight ("The move is" text) for one 90k budget.
- Persona ledger: FLOUR/EMBER 23 = Whitney ceiling exactly; CRUST fainted repeatedly as the grind anchor on Route
  34 (Drowzee/Abra), no whiteout. SUGAR (no damaging move) exempted from the level floor: boxed for gyms.

Next: SQUIRTBOTTLE (Goldenrod flower shop), Route 35/36 (Sudowoodo), Route 37, Ecruteak: Morty (fork
`tallow-pre-morty`; floor = 25-2 = 23 for Gengar L25). Catch GASTLY? not in plan (SMOKE listed but six slots full).

## 2026-09-03 - session tallow-1: HIVE badge (2/8)

**Position:** AZALEA_GYM (4,7). Party: CRUST Geodude L18, FLOUR Pidgey L14, EMBER Quilava L21; SUGAR Togepi L5 in
box 1 (Azalea PC). Money ~¥5.5k. Bag: 3 REPEL, 4 POKé BALL, LURE BALL, TM31, TM49.
Checkpoints: `tallow-r32.state`, `tallow-cave.state`, `tallow-azalea.state`, `tallow-well.state`,
`tallow-well-done.state`, `tallow-pre-bugsy.state`, `tallow-hive.state` (milestone).
- MAREEP does not exist in Crystal's wild tables (`data/wild/*` has no MAREEP): ground slot is CRUST (Geodude,
  Union Cave 1F); electric later = MAGNEMITE (Routes 38/39). REPEL is not sold before Azalea.
- `tallow_lib.boot` now: persona `WANT` species -> nickname encounter hook (2 balls max then flee, via a wrapped
  `_ball_policy`), hatch naming through a wrapped `_take_pending_nickname` (SUGAR hatched named correctly on the
  redo; the first grind pass hatched an unnamed TOGEPI because fight() disarms `_pending_nickname` every battle),
  `trainee_policy(trainee, anchor)` (trainee leads, anchor switches in when outleveled/low) + `set_lead` via
  `d.party_swap`. `tallow_grind.py` = persona leveling loop (lowest-gap mon leads, heal rail, catches on the way).
- Slowpoke Well: the west wing is reached along row 6 ((11,6) -> (6,6)); the grunt at (5,2) is the one whose
  script sets EVENT_CLEARED_SLOWPOKE_WELL. `talk_to` on the rocket girl's corridor (row 4) is severed by her.
- Bugsy: CRUST L16 solo (ROCK THROW), 5 turns. Gym trainers ambush during approach.
- Persona ledger: EMBER L21 vs ceiling 19 (split exp before trainee_policy existed; anchor switch-ins add more).
  SUGAR boxed for the gym instead of being trained to the L14 floor (no damaging move until Metronome L7).

Next: withdraw SUGAR; rival ambush leaving Azalea west; Ilex Forest (Farfetch'd -> HM01 CUT, use 1 REPEL);
Route 34 (train SUGAR by switch-in, catch CRUMB=Sentret); Goldenrod: Whitney (fork `tallow-pre-whitney`).

## 2026-09-03 - session tallow-1: ZEPHYR badge (1/8)

**Position:** VIOLET_GYM (5,2). Party: EMBER Quilava L15, FLOUR Pidgey L9. Money ¥2476. Bag: 8 POKé BALL, TM31.
Checkpoints: `saves/tallow-bedroom.state`, `tallow-starter.state`, `tallow-egg.state`, `tallow-elm-egg.state`,
`tallow-violet.state`, `tallow-pre-falkner.state`, `tallow-zephyr.state` (milestone). Working state `saves/tallow.state`.
- Scripts (all take the state as argv[1]): `scripts/tallow_lib.py` (boot = tactics policy + KO wilds, settle_dialog,
  travel-with-retry, save_clean, heal_at), `tallow_starter.py`, `tallow_egg.py`, `tallow_violet.py`, `tallow_train.py`
  (`d.train` + heal rail), `tallow_gym.py` (heal, fork `tallow-pre-<tag>`, trainers, leader).
- Rival named SILVER (the officer's keyboard was confirmed with the default; the persona names no rival).
- Whiteouts on FORKS only, both undone by reloading: Route 30 (EMBER L6 15/21 pushed on without healing) and
  Route 31 (crossed at 7/34). Rule now in the scripts: heal at the town center before every route leg.
- Persona ledger: bullet 2 ceiling (ace+3 = 12) broken -- `d.train` rotation gives the lead split exp, EMBER went
  10 -> 13 while FLOUR trained. Next time put the trainee in slot 1 (`d.party_swap`) so it fights solo.
- Sprout Tower (HM05 FLASH) skipped for now to hold the level ceiling; still on `missables`.
- Falkner: EMBER solo, 5 turns, TM31 received. Gym trainers ambush on approach (talk_to reports "nothing answered"
  because goto already fought them).

Next: Route 32 (catch MAREEP -> WHISK), 3 REPEL before Union Cave, Azalea / Slowpoke Well / Bugsy (fork `tallow-pre-bugsy`).

## 2026-09-03 - session tallow-1: fresh game claimed

Session tallow-1 owns the whole run; working state `saves/tallow.state`.
`saves/` was empty at start (RUSTY states gone) -> power-on new game as TALLOW.

# (archive) PROGRESS — run RUSTY

Persona contract: `persona_RUSTY.md`.
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
