#!/usr/bin/env python
"""Catch LATIAS -- the Sapphire ROAMER -- by reading the roamer engine.

A chain leg: takes `--state PATH`, mutates it in place, skips itself when
LATIAS is already flagged CAUGHT, and starts from wherever the previous leg
left the save.

WHY THIS IS NOT A PACING HUNT
-----------------------------
LATIAS is not in any wild table. `ROAMER_SPECIES` is `SPECIES_LATIAS` under
`#ifdef SAPPHIRE` (pret/include/constants/species.h:1283) and the engine keeps
exactly one of them in `gSaveBlock1.roamer` (global.h:749, struct at :201-216:
ivs, personality, species, hp, level, status, cool..tough, `active`).

Three reads decide everything, and this script logs all three:

1. `gSaveBlock1.roamer.active`. On the dex148 line it was ZERO -- species 0,
   hp 0, the whole struct blank -- because the roamer is created by
   `special InitRoamer`, which only runs when you WATCH THE TV in your own
   house in Littleroot (pret/data/scripts/tv.inc:43-52). The Hall of Fame sets
   `FLAG_SYS_TV_LATI` (hall_of_fame.inc:33) and
   `CheckForBigMovieOrEmergencyNewsOnTV` (tv.c:2115-2134) returns 1 only in
   `LITTLEROOT_TOWN_BRENDANS_HOUSE_1F` (MAYS_HOUSE_1F if female). So phase one
   is a trip to Littleroot to turn the TV on, and after it the struct is
   LATIAS / level 40 / active.
2. `sRoamerLocation` (roamer.c:16, a FILE-LOCAL EWRAM pair, not in the save
   block -- readable here only because a savestate is a whole-machine
   snapshot). `sRoamerLocations[][6]` (roamer.c:18-41) is 20 map numbers in
   group 0: 0x19 Route110, 0x1A Route111, 0x20-0x31 Route117..Route134.
3. `TryStartRoamerEncounter` (roamer.c:181-192): it fires only when
   `IsRoamerAt(current map)` AND `Random() % 4 == 0`, and in
   `StandardWildEncounter` it is checked INSIDE the normal encounter roll
   (wild_encounter.c:455-461). So one in four encounters on the right route is
   the roamer.

HOW THE ROAMER MOVES -- the whole hunt design comes from this
------------------------------------------------------------
* Walking across a seamless map connection runs `LoadMapFromCameraTransition`
  -> `RoamerMove()` (overworld.c:605,633): ONE step in its own neighbour
  graph.
* A WARP (any door, and every fly) runs `CB2_LoadMap` -> `sub_805493C` ->
  `sub_8053994` -> `RoamerMoveToOtherLocationSet()` (overworld.c:640,662):
  a UNIFORM redraw over the 20 heads, rejecting only its current one.
* Ending a battle runs `CB2_ReturnToFieldLocal` -> `sub_8054A4C`
  (overworld.c:1402-1409), which touches NEITHER. Battles do not move it.

So the cheapest possible search is not a chase at all: stand on ONE route with
grass, step in and out of a door, and read `sRoamerLocation` after each exit.
Every cycle is an independent ~1/19 draw for that route. The Route 117 Poke'mon
Day Care door (51,5) is four tiles from the grass at (49..52,1), which makes
Route 117 (0x20) the base camp.

CATCHING IT: SHADOW TAG, NOT SPEED
----------------------------------
`BattleSetup_StartRoamerBattle` sets `BATTLE_TYPE_ROAMER`, which gives the AI
`aiFlags = 0x20000000` (battle_ai_script_commands.c:333) = bit 29 =
`AI_Roaming` (battle_ai_scripts.s:44). That script is four lines long:

    if_status2 USER, S_TEMP_TRAP, End      @ wrapped -> stays
    if_status2 USER, S_MEAN_LOOK, End      @ mean looked -> stays
    get_ability TARGET; if_equal ABILITY_SHADOW_TAG, End
    get_ability USER;   if_equal ABILITY_LEVITATE, Flee

LATIAS's ability IS Levitate, so it reaches `flee` and leaves on turn one --
and ARENA_TRAP is checked only after Levitate, so a Trapinch would not hold
it. What holds it is the player's ability: WOBBUFFET (box slot 106 on this
line, the only Shadow Tag in the boxes) makes `AI_Roaming` end without ever
choosing to run. The battle then lasts as long as we want it to.

A ball is thrown anyway even without the trap: `SetActionsAndBattlersTurnOrder`
puts every `B_ACTION_USE_ITEM` ahead of everything that is not an item
(battle_main.c:4780-4790), and only the PLAYER's `B_ACTION_RUN` gets the
`var = 5` fast path (:4753) -- the AI's flee does not. So a ball resolves
before the roamer escapes. That is worth exactly ONE throw per encounter,
which at catch rate 3 is hopeless; hence Wobbuffet.

BALLS: TIMER, NOT ULTRA
-----------------------
`atkEF_handleballthrow` (battle_script_commands.c:9450-9460) computes
`odds = (catchRate * ballMultiplier / 10) * (3*maxHP - 2*hp) / (3*maxHP)`.
LATIAS's catch rate is 3, so with integer division:

    ULTRA  mult 20 -> 3*20/10 =  6
    TIMER  mult = min(turn + 10, 40) -> 3*40/10 = 12   (from turn 30 on)

A trapped battle is exactly where a Timer Ball doubles an Ultra Ball, so the
leg buys 99 of them in Rustboro (the only mart that stocks them --
RustboroCity_Mart/scripts.inc:44, behind FLAG_MET_DEVON_EMPLOYEE) plus Ultra
Balls and Hyper Potions in Mossdeep, and throws Ultra for the first ten turns
and Timer after.

NO STATUS, AND CAREFUL CHIP DAMAGE
----------------------------------
The roamer's level-40 moveset is WATER SPORT / REFRESH / MIST BALL / PSYCHIC,
so REFRESH cures anything we inflict: sleep and paralysis are not worth a
turn. HP, though, PERSISTS -- `UpdateRoamerHPStatus` (roamer.c:194-210) writes
hp and status back into the save block when the battle ends. So each encounter
ends by switching to MIGHTYENA (lv87, faster than a level-40 LATIAS, so it
acts before the flee) for one ROCK SMASH, and the next encounter starts with
that damage still gone. It refuses to chip below `CHIP_FLOOR` HP, because
`SetRoamerInactive` fires on `B_OUTCOME_WON` (battle_main.c:5141-5142): a KO
loses the species permanently.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pokeagent.trek import Driver, TravelInterrupted  # noqa: E402
from pokeagent.dex import DexTarget  # noqa: E402


def resolve_battles(d, rounds=4) -> bool:
    """Answer whatever interrupted us. `talk_to` walks with the DEFAULT
    `on_battle="raise"`, so a wild encounter on the way to an NPC comes back
    as TravelInterrupted -- which is how the Route 116 trip died once."""
    for _ in range(rounds):
        if d.in_battle():
            d.fight()
        if d.scene_active():
            d.advance_scene(60000)
        d.close_menus()
        if not d.in_battle() and not d.scene_active():
            return True
    return not d.in_battle()


def talk(d, x, y, facing=None, tries=3) -> bool:
    for _ in range(tries):
        try:
            if d.talk_to(x, y, facing):
                return True
        except TravelInterrupted:
            resolve_battles(d)
            continue
        resolve_battles(d)
    return False


log = logging.getLogger("latias")

#: TWO DIFFERENT NUMBERS, and mixing them cost a false negative: the leg
#: caught LATIAS and still reported `caught=False`. `gSaveBlock1.roamer.species`
#: and `gEnemyParty` use the ROM's internal species index (407), while
#: `DexTarget.dex_flags` answers in NATIONAL DEX numbers (380).
LATIAS_SPECIES = 407
LATIAS_NATDEX = 380
#: THE CAMP HAS TO BE A ROUTE WHERE A DOOR AND GRASS SHARE ONE WALKABLE
#: COMPONENT, and almost none do. Route 117 looked ideal -- the Day Care door
#: is four tiles from grass -- but the only gap in its fence row is (47,5) and
#: the Day Care man STANDS on (47,4) directly above it, so that grass is
#: reachable only by leaving the map through Mauville. `goto` then reported
#: "walked 144 chunks without arriving at (52,1) (now (51,6))" forever.
#: Checked every roamer route: Route 121 is the best of them, with the Safari
#: Zone Entrance door 7 tiles from an 86-cell grass field.
CAMP = "Route121"
CAMP_NUM = 0x24
FLY_HUB = "LilycoveCity"       #: Route121 hangs off Lilycove's left edge
DOOR = (37, 5)                 #: Route121 -> Route121_SafariZoneEntrance
DOOR_STAND = (37, 6)
INNER_DOOR = (14, 13)          #: Route121_SafariZoneEntrance -> Route121
GRASS = ((38, 12), (42, 12))   #: the pacing line, all MB_TALL_GRASS
HOUSE = "LittlerootTown_BrendansHouse_1F"
HOUSE_F = "LittlerootTown_MaysHouse_1F"
TV = (4, 4)                 #: MB_TELEVISION; stand below it and face north

#: Never take the roamer under this many HP with an attack. Its max HP at
#: level 40 is ~120 and ROCK SMASH off a lv87 MIGHTYENA lands ~30, so a floor
#: of 45 cannot be crossed by one chip even on a critical hit.
CHIP_FLOOR = 45
#: Leave before STRUGGLE. Its four moves hold 50 PP between them and struggle
#: recoil would KO it, which is `SetRoamerInactive`.
TURN_CAP = 38
ENEMY_PP_FLOOR = 6


# ---- the three reads ----------------------------------------------------

def roamer(d) -> dict:
    """`gSaveBlock1.roamer`, field by field (global.h:201-216)."""
    base = d.emu.resolve("gSaveBlock1") + d.state.sb1["roamer"]
    raw = d.emu.read(base, 0x14)
    return {
        "ivs": int.from_bytes(raw[0:4], "little"),
        "personality": int.from_bytes(raw[4:8], "little"),
        "species": int.from_bytes(raw[8:10], "little"),
        "hp": int.from_bytes(raw[10:12], "little"),
        "level": raw[12],
        "status": raw[13],
        "active": bool(raw[19]),
        "raw": raw.hex(),
    }


def where(d) -> tuple[int, int]:
    """`sRoamerLocation` -- (map group, map num). Group is always 0."""
    b = d.emu.read("sRoamerLocation", 2)
    return b[0], b[1]


def where_name(d) -> str:
    grp, num = where(d)
    return d.names.map_name(grp, num)


def heads(d) -> list[int]:
    """The 20 map numbers the roamer can occupy, read from the ROM table."""
    raw = d.emu.read("sRoamerLocations", 21 * 6)
    return [raw[i * 6] for i in range(20)]


def caught_ids(d, target) -> frozenset:
    caught, _seen = target.dex_flags(d.state)
    return caught


# ---- phase 1: make the roamer exist ------------------------------------

def activate(d) -> dict:
    """Watch the TV in the player's house so `InitRoamer` runs.

    Returns the roamer struct afterwards. Idempotent: an already-active roamer
    is left alone, because watching again would re-roll its IVs.
    """
    r = roamer(d)
    if r["active"]:
        log.info("roamer already active: %s", describe(d, r))
        return r
    if not d.state.flag("FLAG_SYS_TV_LATI"):
        log.info("FLAG_SYS_TV_LATI is CLEAR and the roamer is inactive -- "
                 "the news has already been watched and this LATIAS is gone")
        return r
    house = HOUSE if d.state.gender() == "male" else HOUSE_F
    log.info("roamer inactive, FLAG_SYS_TV_LATI set -- switching the TV on in %s",
             house)
    if not step_outside(d):
        raise SystemExit("could not reach a flyable tile to start from")
    if not d.fly_to("LittlerootTown"):
        raise SystemExit(f"fly to Littleroot refused: {d.last_fly_reason}")
    if not d.travel(house, on_battle="fight", budget_s=240):
        raise SystemExit(f"could not walk into {house} (at {d.map_name()})")
    if not d.goto(TV[0], TV[1] + 1, on_battle="fight"):
        raise SystemExit(f"could not stand below the TV (at {d.pos()})")
    if not talk(d, TV[0], TV[1], "U"):
        raise SystemExit(f"the TV did not answer an A press: {d.last_talk_reason}")
    d.advance_scene(60000)
    d.flush_dialog()
    d.close_menus()
    r = roamer(d)
    log.info("after the news: %s", describe(d, r))
    return r


def describe(d, r) -> str:
    name = d.names.species(r["species"]) if r["species"] else "none"
    return (f"species={r['species']}({name}) active={r['active']} "
            f"lv={r['level']} hp={r['hp']} status={r['status']} "
            f"at={where_name(d)} raw={r['raw']}")


def surface(d, tries=3) -> bool:
    """Come up from an underwater map.

    A leg inherits the previous leg's position, and on the dex148 line that
    position is `Underwater1 (10,33)` -- where `flight.step_outside` has
    nothing to do and `flyable_here()` is false forever. Surfacing needs a
    tile that is not MB_(SEAWEED_)NO_SURFACING, which `nav.dive_gates(...,
    "emerge")` answers with the engine's own test.
    """
    for _ in range(tries):
        if not d.underwater():
            return True
        here = d.map_name()
        gates = d.nav.dive_gates(here, "emerge")
        if not gates:
            log.info("no emerge tile on %s", here)
            return False
        x, y = d.pos()
        gates.sort(key=lambda c: abs(c[0] - x) + abs(c[1] - y))
        for gate in gates[:6]:
            if d.pos() != tuple(gate) and not d.goto(*gate, on_battle="fight"):
                continue
            if d.dive():
                log.info("surfaced at %s -> %s %s", gate, d.map_name(), d.pos())
                break
            log.info("emerge refused at %s: %s", gate, d.last_field_reason)
        else:
            return False
    return not d.underwater()


def step_outside(d, tries=6) -> bool:
    if not surface(d):
        return False
    for _ in range(tries):
        if d.flight.flyable_here():
            return True
        try:
            if not d.flight.step_outside():
                return False
        except Exception:  # noqa: BLE001 - a wedged exit is a hard stop below
            return False
    return d.flight.flyable_here()


# ---- phase 2: the team and the bag -------------------------------------

def party_species(d) -> list[str]:
    return [d.names.species(m.species) if m and m.species else ""
            for m in d.state.party()]


def bag_count(d, name: str) -> int:
    for pocket in d.state.bag().values():
        if not isinstance(pocket, dict):
            continue
        for held, n in pocket.items():
            if held.upper().replace("é", "E") == name.upper().replace("é", "E"):
                return n or 0
    return 0


def fetch_wobbuffet(d, target) -> bool:
    """Withdraw the boxed WOBBUFFET and make it the lead.

    The lead is what decides whether the roamer flees on turn one, so this is
    not cosmetic -- `AI_Roaming` reads `get_ability TARGET`, and TARGET is
    whatever is standing on the field when the encounter starts.
    """
    from pokeagent.storage import Storage
    from pokeagent.partyorder import PartyOrder

    if "WOBBUFFET" in party_species(d):
        if party_species(d)[0] != "WOBBUFFET":
            PartyOrder(d).lead_with("WOBBUFFET")
        return party_species(d)[0] == "WOBBUFFET"
    slot = None
    for i, mon in target.boxed():
        if d.names.species(mon.species) == "WOBBUFFET":
            slot = i
            break
    if slot is None:
        log.info("no WOBBUFFET in the boxes -- nothing here has Shadow Tag")
        return False
    if not step_outside(d):
        return False
    if not d.fly_to("VerdanturfTown"):
        log.info("fly to Verdanturf refused: %s", d.last_fly_reason)
        return False
    if not d.travel("VerdanturfTown_PokemonCenter_1F", on_battle="fight",
                    budget_s=240):
        log.info("could not reach the Verdanturf centre (at %s)", d.map_name())
        return False
    st = Storage(d)
    party = [p for p in party_species(d) if p]
    if len(party) >= 6:
        # MACHOP is the one party member with no job here: level 25, slower
        # than a level-40 LATIAS, and nothing else in the party is expendable
        # (PELIPPER flies, LOMBRE dives, MIGHTYENA is the chipper).
        drop = None
        for pref in ("MACHOP", "BLAZIKEN", "AGGRON"):
            if pref in party:
                drop = party.index(pref)
                break
        if drop is None:
            drop = len(party) - 1
        log.info("depositing party slot %d (%s)", drop, party[drop])
        if not st.deposit(drop):
            log.info("deposit refused: %s", st.last_reason)
            return False
    if not st.withdraw(slot // 30, slot % 30):
        log.info("withdraw refused: %s", st.last_reason)
        return False
    st.close()
    d.close_menus()
    if not PartyOrder(d).lead_with("WOBBUFFET"):
        log.info("could not lead with WOBBUFFET")
        return False
    d.heal()
    return party_species(d)[0] == "WOBBUFFET"


def shop(d, city: str, mart_map: str, orders: list[tuple[str, int]]) -> None:
    """Buy `orders` at one mart. Missing stock is logged, never fatal."""
    from pokeagent.mart import Mart

    wanted = [(name, qty - bag_count(d, name)) for name, qty in orders]
    wanted = [(n, q) for n, q in wanted if q > 0]
    if not wanted:
        log.info("%s: already stocked", mart_map)
        return
    if not step_outside(d):
        return
    if not d.fly_to(city):
        log.info("fly to %s refused: %s", city, d.last_fly_reason)
        return
    if not d.travel(mart_map, on_battle="fight", budget_s=240):
        log.info("could not reach %s (at %s)", mart_map, d.map_name())
        return
    mart = Mart(d)
    cell = None
    info = d.nav.info(mart_map)
    for obj in getattr(info, "objects", ()) or ():
        if "MART_EMPLOYEE" in str(obj.get("graphics_id", "")):
            cell = (int(obj["x"]), int(obj["y"]))
    if cell is None:
        log.info("no clerk on %s", mart_map)
        return
    # `gMartInfo` SURVIVES THE SHOP CLOSING -- `itemCount` is only rebuilt when
    # the next mart is created (shop.c:123) -- and a savestate preserves it. So
    # `is_open()` can be true the instant we walk in, off the LAST mart's list:
    # this leg read Rustboro's basic list while standing in the MOSSDEEP mart
    # and concluded "HYPER POTION is not sold here", then read it again in
    # Rustboro (flag now set) and said the same about TIMER BALL. The honest
    # test is that the itemList POINTER moved, i.e. this clerk built a list.
    ptr_field = mart.base + mart.layout["itemList"]
    stale = d.emu.u32(ptr_field)
    talk(d, *cell)
    d.settle(120)
    for _ in range(8):
        if mart.is_open() and d.emu.u32(ptr_field) != stale:
            break
        d.emu.run_sequence("A:4 .:40")
    if not mart.is_open():
        log.info("the %s clerk did not open a shop", mart_map)
        d.emu.run_sequence("B:4 .:20 B:4 .:20")
        return
    if d.emu.u32(ptr_field) == stale:
        log.info("%s: gMartInfo.itemList never moved off %#x -- refusing to "
                 "trust a stale list", mart_map, stale)
    log.info("%s sells: %s", mart_map,
             [r["name"] for r in mart.items()])
    for name, target in orders:
        # ONE `buy` CANNOT FILL A STACK. The quantity box is raised one press
        # at a time with a fixed press budget (mart.py:209-216), so a request
        # for 99 settles at 41 -- logged as "the box caps at what the wallet
        # allows", which it is not. Repeat the purchase instead.
        for _ in range(3):
            have = bag_count(d, name)
            if have >= target:
                break
            try:
                ok = mart.buy(name, target - have)
            except Exception as exc:  # noqa: BLE001 - a refused line is not fatal
                ok = False
                log.info("buy %s raised: %s", name, str(exc)[:100])
            log.info("buy %s -> %s (bag now %d/%d)", name, ok,
                     bag_count(d, name), target)
            if not ok or bag_count(d, name) == have:
                break
    mart.leave()
    d.close_menus()


def unlock_timer_balls(d) -> bool:
    """Talk to the Devon employee on Route 116 so the Rustboro mart stocks
    TIMER BALLs.

    `RustboroCity_Mart_EventScript_Clerk` picks its list on
    FLAG_MET_DEVON_EMPLOYEE (RustboroCity_Mart/scripts.inc:9-10), and the only
    thing that sets that flag is the man at Route116 (46,11)
    (Route116/scripts.inc:41). On this line the flag was CLEAR -- the mart
    answered "TIMER BALL is not sold here" -- and his hide flag
    FLAG_HIDE_DEVON_EMPLOYEE_ROUTE116 was clear too, so he is still standing
    there. He also hands over a REPEAT BALL, which is why the buy list below
    asks for one fewer than the pocket can hold.
    """
    if d.state.flag("FLAG_MET_DEVON_EMPLOYEE"):
        return True
    if not step_outside(d):
        return False
    if not d.fly_to("RustboroCity"):
        log.info("fly to Rustboro refused: %s", d.last_fly_reason)
        return False
    if not d.travel("Route116", on_battle="fight", budget_s=300):
        log.info("could not reach Route116 (at %s)", d.map_name())
        return False
    # He has MOVEMENT_TYPE_LOOK_AROUND with a 1-tile range, so his live
    # position is read out of gObjectEvents rather than trusted from the JSON.
    want = (46, 11)
    cell = want
    for npc in d.live_npcs():
        if npc["player"]:
            continue
        if abs(npc["x"] - want[0]) <= 2 and abs(npc["y"] - want[1]) <= 2:
            cell = (npc["x"], npc["y"])
            break
    if not talk(d, *cell):
        log.info("the Devon employee at %s did not answer: %s", cell,
                 d.last_talk_reason)
        return False
    d.advance_scene(60000)
    d.flush_dialog()
    d.close_menus()
    got = d.state.flag("FLAG_MET_DEVON_EMPLOYEE")
    log.info("FLAG_MET_DEVON_EMPLOYEE now %s (REPEAT BALL received=%s)", got,
             d.state.flag("FLAG_RECEIVED_REPEAT_BALL"))
    return got


def prepare(d, target) -> bool:
    ok = fetch_wobbuffet(d, target)
    shop(d, "MossdeepCity", "MossdeepCity_Mart",
         [("ULTRA BALL", 99), ("HYPER POTION", 99)])
    unlock_timer_balls(d)
    shop(d, "RustboroCity", "RustboroCity_Mart", [("TIMER BALL", 99)])
    log.info("bag: TIMER=%d ULTRA=%d GREAT=%d HYPER POTION=%d",
             bag_count(d, "TIMER BALL"), bag_count(d, "ULTRA BALL"),
             bag_count(d, "GREAT BALL"), bag_count(d, "HYPER POTION"))
    return ok


# ---- phase 3: the battle policy ----------------------------------------

class Hunt:
    """One battle policy, shared by every encounter this leg plays."""

    def __init__(self, d, trapper="WOBBUFFET", chipper="MIGHTYENA"):
        self.d = d
        self.trapper = trapper
        self.chipper = chipper
        self.engaged = False        #: a roamer battle happened
        self.balls = 0
        self.chips = 0
        self.turns = 0
        self.last_enemy_hp = None
        self.enemy_max = None
        #: HP the roamer had when the last chip was ordered, so the damage can
        #: be read back out of the save block after the battle ends.
        self.hp_before_chip = None
        self.chip_max = 0
        self.notes: list[str] = []

    @property
    def floor(self) -> int:
        """Refuse to attack below this. A CRIT is 2x in Gen 3, so the bar is
        set from the WORST case of the biggest chip seen so far -- a KO sets
        `SetRoamerInactive` and the species is gone for the whole save."""
        if self.chip_max:
            return max(20, int(self.chip_max * 2.5) + 4)
        return CHIP_FLOOR

    # -- the policy the battle loop calls once per turn --------------------
    def __call__(self, frame):
        enemy = frame.get("enemy") or {}
        name = (enemy.get("species") or "").upper()
        if "LATIAS" not in name:
            return "flee"
        self.engaged = True
        me = frame.get("me") or {}
        mine = (me.get("species") or "").upper()
        hp, mx = enemy.get("hp") or 0, enemy.get("max_hp") or 0
        if self.enemy_max is None and mx:
            self.enemy_max = mx
            self.note(f"LATIAS lv{enemy.get('level')} {hp}/{mx} "
                      f"ability={enemy.get('ability')}")
        self.last_enemy_hp = hp
        # PER BATTLE, NOT CUMULATIVE. `Hunt` is shared by every encounter this
        # leg plays, and `battle.play` resets its own `_turn_no` each time, so
        # carrying a max across battles made the second roamer battle open
        # already past TURN_CAP and leave on turn one.
        self.turns = frame.get("turn") or 0
        pp = sum((m.get("pp") or 0) for m in (enemy.get("moves") or []))

        ball = self.pick_ball(frame)

        # NOT ON THE TRAPPER. Either we switched off it to chip, or it fainted
        # and the lead is somebody else -- and then the roamer WILL flee this
        # turn. A ball still lands, because `SetActionsAndBattlersTurnOrder`
        # runs every USE_ITEM before anything that is not an item
        # (battle_main.c:4780-4790), so an encounter without the trap is worth
        # exactly one throw. Five encounters were thrown away as "balls=0"
        # before this branch existed, with 99 Timer Balls in the bag and a
        # fainted WOBBUFFET at the head of the party.
        if self.trapper not in mine:
            if self.chipper in mine and hp > self.floor:
                slot = self.move_slot(frame, "ROCK SMASH")
                if slot is not None:
                    self.chips += 1
                    self.hp_before_chip = hp
                    self.note(f"chip #{self.chips} at enemy {hp}/{mx} "
                              f"(floor {self.floor})")
                    return ("attack", slot)
            if ball:
                self.balls += 1
                self.note(f"one free throw off {mine} at enemy {hp}/{mx}")
                return ("ball", ball)
            return "flee"

        low = (me.get("hp") or 0) <= 0.55 * (me.get("max_hp") or 1)
        if low and bag_count(self.d, "HYPER POTION"):
            return ("item", "HYPER POTION")
        if ball and pp > ENEMY_PP_FLOOR and self.turns < TURN_CAP:
            self.balls += 1
            return ("ball", ball)

        # Out of balls, or the roamer is about to STRUGGLE itself to death.
        why = ("no balls" if not ball else
               f"enemy pp {pp}" if pp <= ENEMY_PP_FLOOR else
               f"turn {self.turns}")
        # LEAVE BY SWITCHING, NOT BY RUNNING. Fleeing is a speed check the
        # trapper loses to a level-40 LATIAS, so "flee" here meant turn after
        # turn of "Can't escape!" while it was attacked -- which is how
        # WOBBUFFET ended a 14-minute run fainted, and a fainted trapper is
        # the whole strategy gone. Switching to anything else drops Shadow Tag
        # and the roamer leaves of its own accord next turn.
        switchable = frame.get("can_switch") or []
        if hp > self.floor:
            idx = self.party_slot(frame, self.chipper)
            if idx is not None and idx in switchable:
                self.note(f"leaving ({why}) -- switching to {self.chipper} "
                          f"to chip")
                return ("switch", idx)
        for p in frame.get("party") or []:
            idx = p.get("index")
            if idx in switchable and (p.get("species") or "").upper() \
                    != self.trapper:
                self.note(f"leaving ({why}) at enemy {hp}/{mx} -- switching "
                          f"to {p.get('species')} so it runs instead")
                return ("switch", idx)
        self.note(f"leaving ({why}) at enemy {hp}/{mx}")
        return "flee"

    # -- helpers ----------------------------------------------------------
    def note(self, msg):
        log.info("   %s", msg)
        self.notes.append(msg)

    def pick_ball(self, frame):
        """TIMER once its multiplier has grown past ULTRA's, else ULTRA.

        `ball_multiplier = min(battleTurnCounter + 10, 40)` for a Timer Ball,
        against a flat 20 for an Ultra Ball, so the crossover is turn 10.
        """
        balls = (frame.get("bag") or {}).get("poke_balls") or {}

        def have(want):
            for held, n in balls.items():
                if n and held.upper().replace("é", "E") == want:
                    return held
            return None

        order = (["TIMER BALL", "ULTRA BALL"] if self.turns >= 10
                 else ["ULTRA BALL", "TIMER BALL"])
        order += ["GREAT BALL", "REPEAT BALL", "POKE BALL"]
        for want in order:
            held = have(want)
            if held:
                return held
        return None

    @staticmethod
    def move_slot(frame, move_name):
        for m in frame.get("moves") or []:
            if (m.get("name") or "").upper() == move_name and m.get("pp"):
                return m.get("slot")
        return None

    @staticmethod
    def party_slot(frame, species):
        for p in frame.get("party") or []:
            if (p.get("species") or "").upper() == species and p.get("hp"):
                return p.get("index")
        return None


# ---- phase 3: the search loop ------------------------------------------

def walk_to(d, x, y, budget=120.0) -> bool:
    """`goto` with the journey deadline RE-ARMED rather than inherited.

    `Driver._journey_deadline` is an attribute set by callers and never
    cleared, so an expired one from an earlier `travel` makes every later
    `goto` refuse with "journey budget spent" (trek.py:857-863) -- which is
    what pinned this leg at the Day Care door (51,6) with the grass four
    tiles away and the roamer sitting on the route. Armed per call.
    """
    prev = getattr(d, "_journey_deadline", None)
    d._journey_deadline = time.time() + budget
    try:
        return bool(d.goto(x, y, on_battle="fight"))
    except TravelInterrupted:
        resolve_battles(d)
        return d.pos() == (x, y)
    finally:
        d._journey_deadline = prev


def door_cycle(d) -> bool:
    """In and out of the Day Care: two uniform redraws of the roamer."""
    if d.map_name() != CAMP:
        return False
    if d.pos() != DOOR_STAND and not walk_to(d, *DOOR_STAND):
        log.info("   could not reach the door stand cell: %s",
                 d.last_goto_reason)
        return False
    if not d.take_warp(*DOOR):
        return False
    if not d.take_warp(*INNER_DOOR):
        return False
    return d.map_name() == CAMP


def pace(d, hunt, deadline, legs=40) -> bool:
    """Walk the grass line until the roamer battle happens.

    Steps do not move the roamer and neither does a battle, so this can keep
    going for as long as it likes -- but it stops the moment a roamer battle
    ends, because `UpdateRoamerHPStatus` teleports it on the way out.
    """
    a, b = GRASS
    for i in range(legs):
        if hunt.engaged or time.time() > deadline:
            return True
        goal = b if (i % 2 == 0) else a
        if walk_to(d, *goal):
            continue
        log.info("   grass leg to %s refused from %s: %s", goal, d.pos(),
                 d.last_goto_reason)
        resolve_battles(d)
        d.close_menus()
        if not walk_to(d, *goal, budget=180.0):
            log.info("   lost the grass line at %s: %s", d.pos(),
                     d.last_goto_reason)
            return False
    return True



def reach_camp(d) -> bool:
    if d.map_name() == CAMP:
        return True
    return bool(step_outside(d) and d.fly_to(FLY_HUB)
                and d.travel(CAMP, on_battle="fight", budget_s=300))


def restock(d, floor=25) -> None:
    """Refill balls and potions mid-hunt.

    One trapped battle spends ~30 balls and ~10 potions, so a 45-minute leg
    outlives two full pockets. Money is not the constraint (999,999 on this
    line, and Timer Balls are 1000), the walk is -- so it only happens when
    the pocket is nearly dry.
    """
    balls = bag_count(d, "TIMER BALL") + bag_count(d, "ULTRA BALL")
    pots = bag_count(d, "HYPER POTION")
    if balls >= floor and pots >= 8:
        return
    log.info("restocking: %d balls, %d potions left", balls, pots)
    shop(d, "RustboroCity", "RustboroCity_Mart", [("TIMER BALL", 99)])
    shop(d, "MossdeepCity", "MossdeepCity_Mart",
         [("ULTRA BALL", 99), ("HYPER POTION", 99)])
    log.info("restocked: TIMER=%d ULTRA=%d HYPER POTION=%d",
             bag_count(d, "TIMER BALL"), bag_count(d, "ULTRA BALL"),
             bag_count(d, "HYPER POTION"))
    reach_camp(d)


def ensure_trapper(d, hunt) -> bool:
    """Keep the Shadow Tag holder alive and in front.

    THE LEAD IS THE WHOLE STRATEGY. `AI_Roaming` reads `get_ability TARGET`
    at the start of every turn, so if WOBBUFFET is fainted the first healthy
    party member leads and the roamer flees on turn one -- and this leg spent
    five whole encounters that way, logging "balls=0" each time, because
    nothing checked the trapper between battles.
    """
    slot, mon = None, None
    for i, m in enumerate(d.state.party()):
        if m and d.names.species(m.species) == hunt.trapper:
            slot, mon = i, m
            break
    if mon is None:
        return False
    if mon.hp >= 0.6 * mon.max_hp and slot == 0:
        return True
    log.info("%s is %d/%d in slot %d -- healing before the next encounter",
             hunt.trapper, mon.hp, mon.max_hp, slot)
    if not step_outside(d):
        return False
    if not d.heal_at_nearest_center():
        log.info("heal_at_nearest_center failed (at %s)", d.map_name())
        return False
    if slot != 0:
        from pokeagent.partyorder import PartyOrder

        PartyOrder(d).lead_with(hunt.trapper)
    return reach_camp(d)


def hunt_loop(d, target, hunt, deadline, state) -> bool:
    """Returns True once LATIAS is flagged caught."""
    if not reach_camp(d):
        log.info("could not reach %s (at %s)", CAMP, d.map_name())
        return False
    cycles = 0
    while time.time() < deadline:
        if LATIAS_NATDEX in caught_ids(d, target):
            return True
        restock(d)
        ensure_trapper(d, hunt)
        grp, num = where(d)
        if num == CAMP_NUM and d.map_name() == CAMP:
            log.info("roamer IS on %s (%d cycles) -- pacing the grass",
                     CAMP, cycles)
            hunt.engaged = False
            walked = pace(d, hunt, deadline)
            if hunt.engaged:
                r = roamer(d)
                if hunt.hp_before_chip is not None:
                    dealt = hunt.hp_before_chip - r["hp"]
                    if dealt > 0:
                        hunt.chip_max = max(hunt.chip_max, dealt)
                        log.info("   chip dealt %d (worst seen %d, floor now "
                                 "%d)", dealt, hunt.chip_max, hunt.floor)
                    hunt.hp_before_chip = None
                log.info("encounter over: balls=%d chips=%d roamer now %s",
                         hunt.balls, hunt.chips, describe(d, r))
                d.save(state)
                if not r["active"]:
                    log.error("THE ROAMER IS NO LONGER ACTIVE -- it was KO'd "
                              "or caught; nothing else can be done this save")
                    return LATIAS_NATDEX in caught_ids(d, target)
                continue
            if walked:
                continue
            # The grass is unreachable from where we stand and the roamer is
            # RIGHT HERE: re-paced forever without this, because the outer
            # loop's condition is still true. Move the roamer instead.
            log.info("   pacing refused -- cycling the door to move it on")
        cycles += 1
        if cycles % 10 == 0:
            log.info("   cycle %d: roamer at %s", cycles,
                     d.names.map_name(grp, num))
        if not door_cycle(d):
            log.info("   door cycle failed at %s %s -- recovering",
                     d.map_name(), d.pos())
            d.close_menus()
            d.advance_scene(40000)
            if not reach_camp(d):
                return False
    return LATIAS_NATDEX in caught_ids(d, target)


# ---- main ---------------------------------------------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True)
    ap.add_argument("--minutes", type=float, default=35.0)
    ap.add_argument("--phase", default="all",
                    choices=("all", "activate", "prepare", "hunt"))
    ap.add_argument("--out", default=None,
                    help="write a JSON report here")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s latias %(message)s")
    deadline = time.time() + a.minutes * 60

    d = Driver(a.state)
    if d.at_title():
        d.resume_from_title()
    d.advance_scene(40000)
    target = DexTarget(d.emu, d.names, d.consts, d.nav, spec=d.spec)
    before = caught_ids(d, target)
    report = {"dex_before": len(before), "state": a.state}
    log.info("start: dex %d, map %s %s", len(before), d.map_name(), d.pos())

    if LATIAS_NATDEX in before:
        log.info("LATIAS is already CAUGHT -- nothing to do")
        report.update(caught=True, dex_after=len(before))
        finish(a, d, report)
        return 0

    r = roamer(d)
    report["roamer_before"] = {k: v for k, v in r.items()}
    report["roamer_map_before"] = where_name(d)
    log.info("roamer struct: %s", describe(d, r))
    log.info("roamer heads: %s", [f"{n:#x}" for n in heads(d)])

    if a.phase in ("all", "activate"):
        r = activate(d)
        d.save(a.state)
        report["roamer_after_tv"] = {k: v for k, v in r.items()}
        report["roamer_map_after_tv"] = where_name(d)
    if not r["active"]:
        log.info("no active roamer -- stopping with the struct on the record")
        report.update(blocked="roamer inactive", dex_after=len(before))
        finish(a, d, report)
        return 0
    if r["species"] != LATIAS_SPECIES:
        log.info("the roamer is species %d, not LATIAS", r["species"])
        report.update(blocked=f"roamer species {r['species']}",
                      dex_after=len(before))
        finish(a, d, report)
        return 0

    # THE POLICY GOES ON BEFORE THE SHOPPING TRIP. Every walk in `prepare`
    # crosses grass, and without it those encounters are handed to tactics --
    # which fights, levels and can KO something the dex still wants. It also
    # means a roamer met on the way is played properly instead of by default.
    hunt = Hunt(d)
    d.battle_policy = hunt
    if a.phase in ("all", "prepare"):
        prepare(d, target)
        d.save(a.state)
    lead = party_species(d)[0] if party_species(d) else ""
    report["lead"] = lead
    if lead != "WOBBUFFET":
        log.info("lead is %s, not WOBBUFFET -- the roamer will flee on turn "
                 "one and every encounter is worth exactly one ball", lead)

    if a.phase in ("all", "hunt"):
        got = hunt_loop(d, target, hunt, deadline, a.state)
    else:
        got = False
    d.save(a.state)
    after = caught_ids(d, target)
    r = roamer(d)
    report.update(dex_after=len(after), caught=LATIAS_NATDEX in after,
                  balls=hunt.balls, chips=hunt.chips,
                  roamer_end={k: v for k, v in r.items()},
                  roamer_map_end=where_name(d), notes=hunt.notes)
    log.info("done: dex %d -> %d, LATIAS caught=%s, balls=%d, roamer %s",
             len(before), len(after), LATIAS_NATDEX in after, hunt.balls,
             describe(d, r))
    finish(a, d, report)
    return 0


def finish(a, d, report):
    if a.out:
        Path(a.out).write_text(json.dumps(report, indent=1))
    d.save(a.state)


if __name__ == "__main__":
    raise SystemExit(main())
