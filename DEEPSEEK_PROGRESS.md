| ~462000 | ILEX_FOREST | north section reached via CUT tree; ROUTE_34; GOLDENROD_CITY (Quilava lv25) |
| ~470000 | GOLDENROD | Whitney gym: maze mapped, Carrie + Bridget beaten, Whitney vs Miltank = 3 WHITEOUTs |
| ~480000 | AZALEA/ROUTE33 | AZALEA MART sells SUPER_POTION(700)+POTION(300) - bought 1+2; ground to lv29 |
| ~482000 | GOLDENROD_GYM | Whitney BEATEN at lv29 - PLAIN BADGE + TM45 ATTRACT |
| ~484000 | ROUTE_35 | Quilava lv30 (trainer battles); SquirtBottle pending (needs MET_FLORIA from Route 36) |
| ~486000 | ROUTE_36 | MET_FLORIA set (talked to Floria); cut tree at Route 35 (17,6) re-cut; Ecruteak-bound |
| ~488000 | ROUTE_36/GOLDENROD | Sudowoodo at (35,9) blocks the north - needs SQUIRTBOTTLE; back at flower shop, Quilava lv31 |

| `saves/deepseek-flower-shop-squirt-pending.state` | Flower shop, Quilava lv31 24/84 | 3 badges |

Next objective

SQUIRTBOTTLE IS REQUIRED to pass the Sudowoodo at Route 36 (35,9) - it
blocks the route north to Route 37/Ecruteak. The teacher's script needs
MET_FLORIA (set) + TALKED_TO_FLORIA_AT_FLOWER_SHOP (UNSET) + PLAINBADGE
(set). The flower-shop Floria wanders and the player can't catch her -
the position drift makes the manual chase fail. NEXT SESSION: reload
deepseek-flower-shop-squirt-pending and focus on the Floria talk; try
standing at (3,5)/(3,4) and letting her wander adjacent, or reload the
plain-badge state and do the flower shop BEFORE the Route 36 trip.

Gotchas learned (this run)

- Route 36 north is BLOCKED by the Sudowoodo at (35,9) (sprite blocks the
  U from (35,10)); the SudowoodoScript needs the SQUIRTBOTTLE item.
- Route 36: Schoolboy Alan's sprite at (31,14) blocks the R - go around
  via (30,15)->(33,15)->(33,14)->(34,12).

| `saves/deepseek-r36-ecruteak-bound.state` | Route 36 (30,14), Quilava lv30 22/82 | 3 badges |

Next objective

Ecruteak: Burned Tower scene, Morty (Fog Badge). Heal Quilava first (22/82).
SquirtBottle still pending (TALKED_TO_FLORIA_AT_FLOWER_SHOP not set - the
flower shop Floria wanders and the player can't reach her; try again later).

Gotchas learned (this run)

- ROUTE_35 north: the connection to the Route 36 is blocked by a CUT TREE
  at (17,6) - cut it (d.use_cut(17,6)), then walk the col-16 (16,1-5) ->
  (17,1) -> (17,0) -> Route 36. The tree RESPAWNS on state reloads.
- Route 35 navigation: U works from cols 3-4 (the park gate path at (3,5));
  the north-east needs the cut tree. Position drift: wXCoord/OAM disagree,
  steps report "blocked" on walkable cells - re-probe with step_hold.
- ROUTE_36 north (Route 37): the driver's goto replan-storms "npc on target
  cell" at the north edge cells (25-28, 0) - walk manually. The player is
  stuck at (30,14) with U/R blocked (position drift) - needs re-probing.

| `saves/deepseek-r35.state` | Route 35, Quilava lv30 | 3 badges, SquirtBottle + Ecruteak pending |

Next objective

SquirtBottle requires EVENT_MET_FLORIA first (set on ROUTE_36, not the
flower shop): the teacher's script needs MET_FLORIA + TALKED_TO_FLORIA +
PLAINBADGE. Then Route 35 -> Route 36 (Sudowoodo) -> Ecruteak: Burned
Tower scene, Morty (Fog Badge).

Gotchas learned (this run)

- ROUTE_35 navigation fights the position drift: wXCoord/wYCoord and the
  OAM disagree; steps report "blocked" on walkable cells (e.g. U at
  (5,11) blocked though the cell is 0x00). Re-probe with step_hold; the
  Route 36 conn is at (17,0) - the north pocket is reachable only via the
  col-16 (1-5) route which my BFS shows disconnected - verify empirically.

| `saves/deepseek-plain-badge.state` | Goldenrod, 3 badges | QUILAVA lv29, TACKLE/CUT/QUICK ATTACK/EMBER |

Next objective

SquirtBottle from the Goldenrod flower shop (29,5), then Route 35 ->
Ecruteak: Burned Tower scene, Morty (Fog Badge). Heal Quilava (35/79).

Gotchas learned (this run)

- THE WHITNEY WIN: Quilava lv29 (ground from 26 on Route 33 grass at
  (6-11,16-17), ~18 fights) + the fight()'s auto-POTION. The badge scene
  GOTCHA: after the win the crying scene loops "Waaa...You meanie!" -- LEAVE
  the gym and RE-ENTER, then Whitney gives the badge ("What? A BADGE?").
- AZALEA MART sells SUPER_POTION + POTION (the dept-store 2F clerks were
  unreachable; the Azalea mart is right next to the whiteout respawn).
  Manual buy: A on clerk -> shop -> D to item -> A -> qty -> A -> YES.
  The harness mart_buy CRASHES on the shop-open path (UnboundLocalError
  'bought') - buy manually. The B key closes the shop list.
- The driver's `train(target_level)` grinds the lead with auto-healing but
  needs the player already on a grass route; the Route 33 grass at
  (6-11,16-17) works (manual pacing: L/R in the grass).

| `saves/deepseek-whitney-3-whiteouts.state` | Azalea, Quilava lv26 | Whitney BLOCKED |

Next objective

WHITNEY (Plain Badge) is BLOCKED: the Miltank lv20's Rollout + no potions
whips Quilava at lv25-27 (3 whiteouts; one win at lv25 but the badge scene
interrupted by a Bridget whiteout). Options: (a) grind Quilava to lv29-30
on Route 32/34 grass, or (b) buy SUPER_POTIONs at the Goldenrod Dept Store
2F -- the clerks at (13,5)/(13,6) sit in a counter pocket that my BFS says
is unreachable -- VERIFY the clerk approach empirically. Then SquirtBottle
(flower shop), Route 35 -> Ecruteak.

Gotchas learned (this run)

- ILEX FOREST north-south: the ONLY connection is the CUT TREE at (8,25)
  (the two halves are separate walkable regions). `d.use_cut(8,25)` teaches
  HM01 (Quilava IS Cut-compatible) and cuts it. After whiteouts the tree
  RESPAWNS (block back to 0x0f) -- re-cut every trip through.
- Forest ledges walkable + auto-jump 2 tiles: (24,22) HOP_DOWN -> (24,24);
  (15,14)/(15,15) HOP_LEFT. Headbutt trees (0x15) BLOCK.
- Forest south pocket route (verified): col-8 (26-35) -> row-35 (8-14) ->
  col-14 (26-35) -> row-29 (14-22) -> col-22 (28-33) -> row-31 (22-29) ->
  col-29 (22-29) -> row-24 (20-29) -> cut (8,25) -> row-24 (0-9) -> col-0
  (18-24) -> (2,18)->(2,15)->(7,14)->(8,12)->(10,9) -> north maze -> (1,6).
  The BFS pathfinder keeps emitting tree cells as walkable -- walk
  step-by-step and re-BFS from the actual position.
- ROUTE_34 walkable must come from the game's blocks + johto collision
  (nav grid stale: (9,29) is a wall). Goldenrod conn = north edge (8,0).
- GOLDENROD city: the north (gym) and south (PC) street networks are
  SEPARATED by the wall band (rows 11-12). The ONLY crossing is the
  UNDERGROUND (11,29) -> switch room -> UG corridor -> (9,5). The DRIVER's
  cross-map goto handles this crossing (manual BFS can't).
- Goldenrod gym maze: Whitney at (8,3) -- her sprite blocks the cell; stand
  at (8,4) and TALK. wXCoord/wYCoord DRIFT in the gym -- use OAM + scroll.
- WHITNEY: Clefairy lv18 + Miltank lv20; Rollout ramps 30/60/120 -- no
  potions = whiteout at lv25-27. After ANY whiteout the player respawns at
  the AZALEA PC, NOT the goldenrod.
- Dept Store 2F: escalator at (15,0); potion clerks at (13,5)/(13,6) appear
  isolated by counter blocks (0x90). Verify the approach empirically.

Gotchas learned (this run)

- ILEX FOREST IS A MAZE: the north and south sections are SEPARATE
  walkable regions. The ONLY connection is the CUT TREE at (8,25) --
  use `d.use_cut(8, 25)` (teaches HM01 to Quilava, deleting LEER; Quilava
  IS Cut-compatible in this build). Without cutting it, BFS finds NO path
  to the north exit (1,5).
- The forest's ledges ARE walkable and auto-jump (2-tile hop): (24,22) is
  a HOP_DOWN ledge -- step onto it, face DOWN, and the next step lands at
  (24,24). (15,14)/(15,15) are HOP_LEFT ledges. The row-22 ledge run
  (23-26,22) connects the row-23/24 pockets to the row-24 floor.
- The game's collision bytes: 0x15 (HEADBUTT_TREE) = WALL_TILE|TALK --
  headbutt trees BLOCK movement (permission table
  data/collision/collision_permissions.asm). "Blocked" steps near grass
  are usually battle-intros, not walls: probe with step_hold + fight.
- Forest south pocket routing (verified): col-8 (26-35) -> row-35
  (8-14) -> col-14 (26-35) -> row-29 (14-22) -> col-22 (28-33) -> row-31
  (22-29) -> col-29 (22-29) -> row-24 (20-29) -> cut tree at (8,25) ->
  row-24 (0-9) -> col-0 (18-24) -> (2,18)->(2,15)->(7,14)->(8,12)->
  (10,9) -> north maze -> (1,6) -> step U onto (1,5) warp.
- The ROUTE_34_ILEX_FOREST_GATE: the forest warp lands at the gate's
  south end; walk U through the gate to (4,0)/(5,0) -> Route 34 north
  side at ~(14,29). The Route 34 walkable must come from the game's
  blocks + johto collision (the nav grid is stale there: (9,29) is a
  wall, not floor).
- Route 34 trainers sight-trigger ("Are you a trainer?"): flush the
  dialog, then fight(). Route 34 -> Goldenrod: walk north to (8,0) and
  step U (the conn is GOLDENROD_CITY).
- d.use_cut(x, y) does everything: teaches HM01 (forgets the named move),
  approaches the tree, uses CUT via START->POKéMON, verifies the block
  replaced (0x0e -> 0xe0 at the cut cell) and steps onto it.

| `saves/deepseek-cut-obtained.state` | Ilex done, Cut obtained | |

Next objective

Route 34 -> Goldenrod: heal, Whitney (Plain Badge), SquirtBottle, then
Route 35 -> Ecruteak.

| `saves/deepseek-cut-obtained.state` | Ilex done, Cut obtained | |

Next objective

Route 34 -> Goldenrod: heal, Whitney (Plain Badge), SquirtBottle, then
Route 35 -> Ecruteak.

Gotchas learned (this run)

- THE BIG ONE: fight() can WHITEOUT if invoked before the battle mon is
  populated -- the battle intro must reach the FIGHT menu first (my
  inspections read me() too early). The rival/Croconaw losses were REAL
  (underleveled + no potions); train and heal before tough fights.
- Farfetch'd chase: wFarfetchdPosition 1-10, each talk moves it; the
  FACING determines the branch (some send it BACK). Correct facings:
  pos4->5: face DOWN; pos5->6: face DOWN; pos6->7: not RIGHT; pos7->8:
  not DOWN/LEFT; pos8->9: face DOWN; pos9->10: face UP or LEFT.
  Approach each position from the cell facing it with the right facing.
- HM01 Cut received from the charcoal master after the Farfetch'd chase.

| `saves/deepseek-hive-badge.state` | Azalea gym done | badge 2 |

Next objective

Ilex Forest (get Cut HM), Route 34, Goldenrod: beat Whitney (Plain Badge),
SquirtBottle, then Ecruteak.

Gotchas learned (this run)

- THE BIG ONE: the nav's side-wall model had the directions INVERTED vs
  home/map.asm AND the game's empirical behavior. Reverted to the
  original (0xb2 blocks D entry). Union Cave traversal needed manual
  step-by-step walking (the nav grid was stale for the cave + side walls).
- The nav grid cache can be overwritten per-map from the ROM's
  wMapBlocksPointer + tileset collision when the repo .blk is stale.
- WALKABLE-CELL TRUTH: screen glyphs lie; probe the game. Verify cell
  collisions from the ROM blocks (wMapBlocksBank/ptr + tileset table)
  and confirm against step probes.
- Trainer battles: a "blocked" step near a trainer = the intro dialog
  fired; flush the textbox THEN the battle engages. Sometimes the dialog
  is multi-page and flush loops exit early (textbox flicker) -- loop
  until mode==0 or battle.
- Defeated trainers' sprites REMAIN and block (twins at Azalea Gym block
  the center corridor). The gym maze: walk the nav-grid path; the direct
  center column is blocked by Benny -- go around via the top.
- Bugsy's sprite blocks his cell: stand adjacent (6,7), face him, talk.
- The Azalea Gym is 16x10 tiles; the nav grid (10 wide) matched the
  block-derived truth once miscounting was corrected.

| `saves/deepseek-well-cleared.state` | Azalea, well done | Kurt gave Lure Ball |

Next objective

Beat Bugsy (Hive Badge), Ilex Forest (Cut), Goldenrod -> Whitney.

Gotchas learned (this run)

- THE BIG ONE: the nav's side-wall model had the directions INVERTED vs
  home/map.asm AND the game's empirical behavior. Reverted to the
  original (0xb2 blocks D entry). Union Cave traversal needed manual
  step-by-step walking (the nav grid was stale for the cave + side walls).
- The nav grid cache can be overwritten per-map from the ROM's
  wMapBlocksPointer + tileset collision when the repo .blk is stale.
- WALKABLE-CELL TRUTH: screen glyphs lie; probe the game. Verify cell
  collisions from the ROM blocks (wMapBlocksBank/ptr + tileset table)
  and confirm against step probes.
- Trainer battles: a "blocked" step near a trainer = the intro dialog
  fired; flush the textbox THEN the battle engages. Sometimes the dialog
  is multi-page and flush loops exit early (textbox flicker) -- loop
  until mode==0 or battle.
- Well warp landing drifts; entrance pocket is a dead end; the real path
  goes through the east column -> (14,4) -> upper area.
- Kurt's scene at the well auto-warps the player to Kurt's house when
  cleared (the last grunt's defeat triggers it).

| `saves/deepseek-zephyr-badge.state` | Violet gym done | badge 1 |

Next objective

Route 32 -> Union Cave -> Azalea Town: Slowpoke Well event, beat Bugsy (Hive
Badge), Cut HM in Ilex Forest.

Gotchas learned (this run)

- nav grid MATCHES the ROM here; the driver's step detection fails on
  ledge hops and mid-transition camera reads (hSCX/hSCY) are garbage --
  when the driver reports "unexplained blocked step", walk manually with
  step_dir/step_hold (hold ~90 frames on hop cells).
- Route 31 -> Violet is via the ROUTE_31_VIOLET_GATE at the WEST side
  (warp (4,6)), NOT the north road (that dead-ends at Dark Cave).
- Whiteout on a cave wild costs half your money (3000 -> 1738) and heals
  you in place; a cave at low HP is a real wipe risk -- heal first.
- heal_pokecenter stops at the nurse's yesorno box: confirm A manually.
- `d.fight()` handles level-up learn prompts (Ember kept at lv12).
- Gym trainer battles during goto: driver fights them automatically.

| `saves/deepseek-errand-done.state` | Cherrygrove, rival beaten | errand complete |

Next objective

Back to New Bark -> Elm's lab (officer scene, 5 Poke Balls from aide),
then Route 29 -> Violet City, train, beat Falkner (Zephyr Badge).

Gotchas learned (this run)

- This build (modified pokecrystal): new game asks gender (BOY/GIRL), clock
  (hour/min + DST + time confirm), and starter nickname prompt.
- The screen_text charmap decodes Pokémon front pics as letter-grid garbage
  (7x7 box "AHOV:dk" was CYNDAQUIL's pokepic, NOT a keyboard).
- page_wait hook fires late/never on some prompt pages; poll-based
  mash_dialog with `textbox_up` + `_cursor_outside_box` guards is the
  reliable advance (but a mash left running with no textbox burns frames
  and its stray A can re-open an NPC's dialog -- always bound it).
- textbox_up() FALSE-POSITIVES on indoor floor tiles (lab bottom rows
  decode to ▃▘ glyphs): use real_textbox() = border row check (rows 12/13
  have ┌ and row 17 has └) for indoor maps.
- goto() to an exit-warp cell short-circuits via the arrival-proximity
  check (returns True while still inside): walk to the cell above the
  door, then step_hold onto the warp.
- Scene coord_event cells are nav-blocked while active: walk through them
  manually with step_dir so the real scene fires (aide potion, rival).
- Wild battles happen during travel legs; the Driver auto-fights them.
- Don't rebind the `d` Driver variable in loop iterators (kernel state!).

| File | Where | Notes |
|---|---|---|
| `saves/deepseek.state` | live | mutating working state |
| `saves/deepseek-starter.state` | ELMS_LAB, just got Cyndaquil | first milestone |

Next objective

Finish Elm's lab scene (directions), rival battle on R29, Mr. Pokemon errand,
Pokedex + 5 balls, then Violet City and Falkner.

Gotchas learned (this run)

- This build (modified pokecrystal): new game asks gender (BOY/GIRL), clock
  (hour/min + DST + time confirm), and starter nickname prompt.
- The screen_text charmap decodes Pokémon front pics as letter-grid garbage
  (7x7 box "AHOV:dk" was CYNDAQUIL's pokepic, NOT a keyboard).
- page_wait hook fires late/never on some prompt pages; poll-based
  mash_dialog with `textbox_up` + `_cursor_outside_box` guards is the
  reliable advance (but a mash left running with no textbox burns frames
  and its stray A can re-open an NPC's dialog -- always bound it).
- YesNoBox A presses land during box setup: confirm-until-closed pattern.
- `d.save()` refuses to overwrite a NEWER .meta frame; fine for milestones.
|  |  |  |

## Checkpoints

| File | Where | Notes |
|---|---|---|
| `saves/deepseek.state` | live | mutating working state |

## Next objective

Play intro: NEW GAME → name player → name rival → wake in bedroom.

## Gotchas learned (this run)

- (none yet)
