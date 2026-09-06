#!/usr/bin/env python
"""Two stone evolutions that nothing else in the chain covers: RAICHU and
DELCATTY. Runs as a `chain.py` leg -- `--state` is mutated IN PLACE, every
species already flagged CAUGHT is skipped, and no starting map is assumed.

    ITEM_THUNDER_STONE + PIKACHU -> RAICHU     (evolution.h:20)
    ITEM_MOON_STONE    + SKITTY  -> DELCATTY   (evolution.h:151)

ITEM NAMES ARE ASKED OF THE CARTRIDGE, NEVER TYPED. `gItems[96].name` on this
ROM is `THUNDERSTONE` -- ONE WORD -- while every other stone carries a space
(`MOON STONE`, `WATER STONE`, `FIRE STONE`). A table spelling it
"THUNDER STONE" matches nothing in the bag, so a successful trade reads as a
failure and the stone is reported absent while it sits in the ITEMS pocket.
`shard_trade.item_name` resolves the display name from the item id, so there
is nothing here to spell wrong.

WHERE THE TWO STONES COME FROM, verified against this save and this ROM
rather than a walkthrough:

* THUNDERSTONE is REPEATABLE and cheap. The Route 124 treasure hunter swaps
  one Yellow Shard for one Thunderstone (Route124_DivingTreasureHuntersHouse
  scripts.inc:88-223), and the Yellow Shard item ball at Route124 (58,11) is
  still on the ground on this line: its guard flag `FLAG_ITEM_ROUTE124_1`
  reads False, where the Red (`_2`) and Blue (`_3`) balls both read True --
  those two paid for NINETALES and STARMIE earlier in the session. Cost: one
  item ball, no money.

* MOON STONE has NO purchasable and no remaining ground source, and both of
  the standing claims about that check out:

  1. The game's only Moon Stone item ball is `finditem ITEM_MOON_STONE`
     (data/item_ball_scripts.inc:305-307) in MeteorFalls_1F_1R, and
     `FLAG_ITEM_METEOR_FALLS_1F_1R_3` reads **True** on this save -- taken.
     `MOON_STONE` appears nowhere else in data/*.inc or any map.json, so
     there is no hidden one either.
  2. Lilycove Department Store 5F does NOT sell it, even though its shelf
     list is literally spelled `.2byte ITEM_MOON_STONE`
     (LilycoveCity_DepartmentStore_5F/scripts.inc:23). All four clerks run
     `pokemartdecoration2`, which is `ScrCmd_pokemartdecoration2` ->
     `Shop_CreateDecorationShop2Menu` -> `CreateShopMenu(MART_TYPE_2)`
     (scrcmd.c:1773-1780, shop.c:1216-1218): a DECORATION shop, whose list
     entries are DECOR_ ids. ITEM_MOON_STONE is 94 and decoration 94 is
     `DECOR_KECLEON_DOLL` (constants/decorations.h:94), so that clerk sells a
     Kecleon Doll. Same trap for ids 93-98 (SUN..LEAF STONE by item number,
     BALTOY..GULPIN DOLL by decoration number).

  That leaves the held item on a wild LUNATONE, which is the only Moon Stone
  carrier this game can roll: `gBaseStats[LUNATONE]` has `item1 = ITEM_NONE`
  and `item2 = ITEM_MOON_STONE` (base_stats.h:10739; confirmed live off
  `gBaseStats + species*stride + 0x0E`). The only other carriers are the
  Clefairy line, which is unobtainable in Sapphire. `SetWildMonHeldItem`
  (pokemon_3.c:1320-1339) rolls `Random() % 100` and only assigns `item2`
  when the roll is 95-99, so it is a flat **5%**.

  Best odds are MeteorFalls_B1F_1R, where LUNATONE owns land slots 3,4,5,7 =
  10+10+10+5 = **35%** of encounters, against 20% in the entrance room
  (measured off the live `gWildMonHeaders`, not the JSON, because that file
  carries Ruby's tables behind an #ifdef). 0.35 * 0.05 = one Moon Stone per
  ~57 encounters, and land `encounterRate` is 10 there, so budget generously.

  `gBattleMons` is PLAINTEXT -- the Gen 3 substructure encryption covers only
  the boxed struct -- so the enemy's held item is readable BEFORE any
  decision. That turns a 5% drop into a 5% *inspection*: flee the 95%, and
  spend balls only on a confirmed carrier.

WHY THIS SCRIPT FREES ITS OWN PARTY SLOT
----------------------------------------
Both pre-evolutions are BOXED, a stone is used on a PARTY member, and the
party is full -- so a slot has to be given up. `shard_trade.make_room` and
`stone_evos.free_party_slot` both deposit the lowest-level mon that is
carrying nothing, and on this line that is LOMBRE L52: the party's ONLY
WATERFALL, DIVE and STRENGTH knower. Boxing it seals the deeper Meteor Falls
rooms this very leg depends on, and takes RELICANTH's dive away from the
`underwater` leg that runs after this one in the chain.

Both of those helpers return True immediately when the party is already below
six, so this script frees a slot FIRST, choosing a victim that is not the
sole holder of any field move, and they then never get to pick. No sibling
script is modified.

WHAT IT COST, MEASURED
----------------------
RAICHU: one YELLOW SHARD. The ball at Route124 (58,11) is sealed behind reef,
so the leg dived Route124 (65,19) -> Underwater1 -> surfaced at (68,9),
picked it up and traded it. No money.
DELCATTY: 18 encounters and 12 ULTRA BALLs. The sixth LUNATONE was carrying
(L33, MeteorFalls_B1F_1R); the stone came off it with `take_from_mon` and the
mon itself then paid for SKITTY's party slot. Dex 148 -> 149 -> 150.

Usage -- in place, idempotent, safe to re-run:

    scripts/stones2.py --state saves/st2.state
    scripts/stones2.py --state saves/st2.state --only raichu
"""
import argparse
import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from pokeagent import nav as nav_mod  # noqa: E402
from pokeagent.storage import Storage  # noqa: E402
from pokeagent.trek import Driver, TravelInterrupted  # noqa: E402

# Proven machinery, imported rather than re-derived. `shard_trade` owns the
# shard->stone trade (including the dive to a ball no amount of surfing
# reaches) and the withdraw-and-spend; `stone_evos` owns the LUNATONE hunt and
# the dex reads. Both are frozen, working scripts and neither is edited here.
from shard_trade import (  # noqa: E402
    _enable_surf, _has, find_colour, item_name, spend, to_overworld, trade,
)
from share_grind import unwedge  # noqa: E402
from stone_evos import (  # noqa: E402
    collector, dex_count, owns, pace_cells, reach, registered,
    strip_moon_stone,
)

log = logging.getLogger("stones2")

#: leg -> (pre-evolution, post-evolution, stone constant). RAICHU first: it is
#: one item ball and a conversation, where DELCATTY is a 1.75%-per-encounter
#: hunt, so an interrupted run still banks the certain species.
LEGS = {
    "raichu": ("PIKACHU", "RAICHU", "ITEM_THUNDER_STONE"),
    "delcatty": ("SKITTY", "DELCATTY", "ITEM_MOON_STONE"),
}
ORDER = ("raichu", "delcatty")

#: A mon that is the only one who knows one of these does not go in a box.
#: Read as MOVE display names, which is what `names.move` hands back.
FIELD_MOVES = ("SURF", "FLY", "WATERFALL", "DIVE", "STRENGTH", "ROCK SMASH")

#: Where a slot gets freed, per leg: a Fly town's Center near that leg's work,
#: so the trip is a flight and a few steps instead of a whole-region
#: pathfind. Ordered, and always followed by FALLBACK_CENTERS.
CENTERS = {
    "raichu": ("LilycoveCity_PokemonCenter_1F",),
    "delcatty": ("FallarborTown_PokemonCenter_1F",),
}

#: Tried after a leg's own Center. LilycoveCity is first because its PC is
#: the one that has actually been driven on this line; FallarborTown's nav
#: could not reach the cell below its PC at all.
FALLBACK_CENTERS = (
    "LilycoveCity_PokemonCenter_1F",
    "MossdeepCity_PokemonCenter_1F",
    "RustboroCity_PokemonCenter_1F",
)

#: Meteor Falls rooms, RICHEST LUNATONE SHARE FIRST, measured off the live
#: `gWildMonHeaders` rather than wild_encounters.json (which carries Ruby's
#: tables behind an #ifdef). Land slots 3,4,5,7 of the deep rooms are
#: LUNATONE -- 10+10+10+5 = 35% -- against slots 5,6,7 of the entrance room,
#: 20%. At a 5% hold rate that is one stone per 57 encounters instead of one
#: per 100. Level costs nothing: it does not enter the Gen 3 catch formula,
#: and L33-39 is still twelve levels short of LUNATONE's EXPLOSION.
MOON_MAPS = ("MeteorFalls_B1F_1R", "MeteorFalls_1F_2R", "MeteorFalls_1F_1R")

#: A full-HP LUNATONE has catch rate 45, so an ULTRA BALL is roughly one in
#: nine and one carrier costs ~9 balls. The bag holds 75 and there is no
#: Master Ball on this line.
BALL = "ULTRA BALL"


def surface(d, tries: int = 12) -> bool:
    """Get off MAP_TYPE_UNDERWATER, walking to a surfacable ceiling first.

    THIS SAVE BOOTS UNDERWATER and nothing in the harness could lift it. Fly
    is refused on MAP_TYPE_UNDERWATER by
    `Overworld_MapTypeAllowsTeleportAndFly`; `Driver.fly_to` knows that and
    calls `dive()` first (trek.py:2892-2899), but `Flight.step_outside` only
    knows warps (flying.py:844-863) and a dive is not a warp -- so
    `Collector.goto_map`, which is what every "get to a Centre" path here
    ends in, reported:

        fly: indoors -- Underwater1 is MAP_TYPE_UNDERWATER, which
        Overworld_MapTypeAllowsTeleportAndFly refuses
        no route to LilycoveCity_PokemonCenter_1F from Underwater1

    and the leg died before touching anything. `dive()` was right to refuse
    as well: the player stands on Underwater1 (10,33), whose behavior reads
    42 = 0x2A = MB_SEAWEED_NO_SURFACING, and `MetatileBehavior_IsNotSurfacable`
    forbids coming up there (nav.py:70). So the ceiling has to be walked to.

    `nav.dive_gates(map, "emerge")` is the engine's own test for a cell one
    can surface from, and `nav.surfing` must be set or nav counts every
    underwater cell -- all of them water -- as a wall.
    """
    if not d.underwater():
        return True
    _enable_surf(d)
    # Wild CLAMPERL and CHINCHOU roll on the way to the ceiling, and a KO
    # fight per encounter is minutes wasted for nothing this leg wants.
    d.battle_policy = lambda _f: "flee"
    try:
        for _ in range(3):
            if not d.underwater():
                return True
            if d.dive() and not d.underwater():
                log.info("[surface] up from %s", d.map_name())
                return True
            here = d.map_name()
            gates = set(d.nav.dive_gates(here, "emerge"))
            try:
                reach = set(d.nav.reachable(here, d.pos(), d.elevation()))
            except Exception as exc:  # noqa: BLE001
                log.info("[surface] reachable(%s): %s", here, str(exc)[:90])
                reach = set()
            px, py = d.pos()
            cands = sorted(gates & reach,
                           key=lambda c: abs(c[0] - px) + abs(c[1] - py))
            if not cands:
                log.info("[surface] no reachable ceiling on %s (%d gates, "
                         "%d reachable)", here, len(gates), len(reach))
                return False
            log.info("[surface] %s (%d,%d) is %s; nearest ceiling of %d is %s",
                     here, px, py,
                     "seaweed/no-surfacing" if d.last_field_reason else "?",
                     len(cands), cands[0])
            for cell in cands[:tries]:
                # PER-CALL AND CLEARED: `Driver._journey_deadline` persists
                # across calls and an EXPIRED one makes `take_warp` refuse
                # every approach cell for the rest of the run.
                d._journey_deadline = time.time() + 90.0
                try:
                    moved = d.goto(*cell)
                except Exception as exc:  # noqa: BLE001
                    log.info("[surface] goto %s: %s", cell, str(exc)[:90])
                    moved = False
                finally:
                    d._journey_deadline = None
                if not moved:
                    continue
                if d.dive() and not d.underwater():
                    log.info("[surface] up at %s -> %s %s", cell,
                             d.map_name(), d.pos())
                    return True
                log.info("[surface] dive at %s refused: %s", cell,
                         d.last_field_reason)
    finally:
        d.battle_policy = None
    return not d.underwater()


def _moves(d, mon) -> set:
    out = set()
    for mid in (getattr(mon, "moves", None) or ()):
        if not mid:
            continue
        try:
            out.add(d.names.move(mid).upper())
        except Exception:  # noqa: BLE001
            continue
    return out


def sole_field_movers(d) -> set:
    """Party indices that must not be deposited.

    A mon is protected when it is the ONLY party member knowing some field
    move: boxing it strands the save. Counting per move rather than blanket-
    protecting every HM knower means a second SURFer is still expendable.
    """
    party = [(i, m) for i, m in enumerate(d.state.party()) if not m.is_egg]
    known = {i: _moves(d, m) for i, m in party}
    protect = set()
    for move in FIELD_MOVES:
        holders = [i for i, mv in known.items() if move in mv]
        if len(holders) == 1:
            protect.add(holders[0])
    return protect


def to_pc(d, centers=()) -> bool:
    """Stand at a Pokemon Center whose PC nav can actually reach.

    TWO SEPARATE LANDMINES, both measured on this line, are why this is not
    just `goto_map`:

    * `shard_trade.spend` calls `share_grind.to_center` when the target is
      boxed, and that calls `heal_at_nearest_center` ->
      `nav.route_legs` -> `usable_exits` -> `reachable`. Called from an
      INDOOR map it never returns in useful time: from inside
      Route124_DivingTreasureHuntersHouse the StallWatch reported "wedged at
      Route124_DivingTreasureHuntersHouse (5,5) for 150s ... nothing is
      ticking the core and the process is blocked in Python", with the stack
      inside `nav.step`. `to_center` returns True IMMEDIATELY when the map
      already ends in `PokemonCenter_1F` (share_grind.py:99-101), so the fix
      is to be standing in one before `spend` is ever called.

    * The PC is not always reachable once inside. FallarborTown's Center
      answered "could not reach the cell below the PC at (10,1): stalled 12x
      at (10,3) heading for (10,2)" -- nav walking a SHIPPED layout that the
      game-clear `setmaplayoutindex` has rewritten. `sync_grid` is the
      documented remedy, and a second Center is tried when it is not enough,
      because one deposit is not worth losing the leg over.
    """
    unwedge(d)
    if d.map_name().endswith("PokemonCenter_1F"):
        try:
            d.sync_grid()
        except Exception:  # noqa: BLE001
            pass
        if Storage(d).pc_cells():
            return True
    for center in (tuple(centers) + FALLBACK_CENTERS):
        if d.map_name() != center:
            if not collector(d).goto_map(center, budget=300.0):
                log.info("[pc] could not reach %s (%s)", center,
                         getattr(d, "last_goto_reason", "?"))
                continue
        try:
            log.info("[pc] %s: sync_grid changed %d cells", center,
                     d.sync_grid())
        except Exception as exc:  # noqa: BLE001
            log.info("[pc] sync_grid: %s", str(exc)[:90])
        if Storage(d).pc_cells():
            return True
        log.info("[pc] no usable PC on %s", d.map_name())
    return d.map_name().endswith("PokemonCenter_1F")


def free_slot(d, centers=()) -> bool:
    """Make sure the party has room, without stranding the save.

    The victim carries NOTHING (a mon deposited while holding an item takes
    the item into the box with it, and one party member is holding another
    agent's EXP. SHARE) and is not a sole field mover. Lowest level first,
    since level is the only thing here that is cheap to replace.
    """
    party = [m for m in d.state.party() if not m.is_egg]
    if len(party) < 6:
        return True
    protect = sole_field_movers(d)
    cands = [(i, m) for i, m in enumerate(d.state.party())
             if not m.is_egg and not m.held_item and i not in protect]
    if not cands:
        held = [(d.names.species(m.species), bool(m.held_item))
                for m in d.state.party() if not m.is_egg]
        log.info("[slot] no safe victim: protected=%s party=%s",
                 sorted(protect), held)
        return False
    i, victim = min(cands, key=lambda p: p[1].level or 0)
    if not to_pc(d, centers):
        log.info("[slot] no Center with a reachable PC (at %s)", d.map_name())
        return False
    st = Storage(d)
    log.info("[slot] depositing %s L%s (holds nothing, not a sole field "
             "mover)", victim.nickname, victim.level)
    ok = st.deposit(i)
    st.close()
    if not ok:
        log.info("[slot] deposit refused: %s", getattr(st, "last_reason", "?"))
    return ok


# ---- the Moon Stone hunt ----------------------------------------------


def moon_map(d) -> str:
    """The Meteor Falls room with the richest LUNATONE share we can enter."""
    for name in MOON_MAPS:
        if d.map_name() == name:
            return name
        if reach(d, name, budget=300.0):
            return d.map_name()
    return d.map_name()


def table_species(d, map_name: str) -> set:
    """Species this map's LAND table can actually roll."""
    try:
        from stone_evos import dex_view

        return {d.names.species(r.species)
                for r in dex_view(d).wild.for_map(map_name)
                if r.kind == "land"}
    except Exception as exc:  # noqa: BLE001 - the gate is a nicety
        log.info("[moon] wild table for %s: %s", map_name, str(exc)[:90])
        return set()


def moon_encounter(d, moon_id: int, seen: dict, allowed: set) -> str:
    """Play one wild encounter, decided on the enemy's HELD ITEM.

    `gBattleMons` is plaintext (tactics.py:289-291 -- Gen 3's substructure
    encryption covers only the boxed struct), so the enemy's item is readable
    before any decision is taken. That turns a 5% drop into a 5%
    *inspection*: 95% of encounters cost a RUN and no ball at all.

    THE TEXT HAS TO BE ADVANCED FIRST, and this is the whole reason this
    function exists instead of `stone_evos.settled_enemy`. That one waits for
    `battle.at_action_menu()` by TICKING the core and never pressing
    anything -- but the menu is controller-based and the turn's opening text
    blocks it until an A press: "The turn's opening text ('Wild POOCHYENA
    appeared!' ...) blocks the menu, and it only advances on A"
    (battle.py:1308-1332). So it never saw a menu, returned None, the caller
    counted an encounter and `continue`d, and the hunt span on ONE battle for
    thirty minutes -- live feed stuck at `in_battle: True`, `message: "Wild
    LUNATONE appeared!"`, position frozen at MeteorFalls_B1F_1R (15,12), and
    not one line of output because the tally log sat past the `continue`.
    `battle.await_action_menu` is the library's own answer and it presses A.
    """
    if not d.battle.await_action_menu():
        # Not something we can act in -- hand it to the harness and move on
        # rather than deciding about a mon we never read.
        log.info("[moon] no action menu (%s); running",
                 getattr(d.battle, "last_reason", "?"))
        policy = lambda _f: "flee"  # noqa: E731
        d.battle_policy = policy
        try:
            d.fight(policy=policy)
        except Exception as exc:  # noqa: BLE001
            log.info("[moon] fight: %s", str(exc)[:120])
        finally:
            d.battle_policy = None
        return "unreadable"

    enemy = d.battle.battler(1)
    name = getattr(enemy, "name", "?")
    item = int(getattr(enemy, "item", 0) or 0)
    if allowed and name not in allowed:
        # The stale-`gEnemyParty` bug's fingerprint. Worth a line, not a
        # bail: the action menu being up means the intro has finished.
        log.info("[moon] %s is not in %s's land table %s -- reading it "
                 "anyway", name, d.map_name(), sorted(allowed))
    seen[name] = seen.get(name, 0) + 1
    carrying = item == moon_id
    if carrying:
        log.info("[moon] *** %s L%s IS HOLDING %s -- throwing %s ***", name,
                 getattr(enemy, "level", "?"), d.names.item(item), BALL)
        policy = lambda _f: ("ball", BALL)  # noqa: E731
    else:
        policy = lambda _f: "flee"  # noqa: E731
    # BOTH, not just the argument: `goto` plays encounters itself and consults
    # `battle_policy` (trek.py:3183-3184), so a battle that starts inside a
    # walk leg is decided the same way as one we opened ourselves.
    d.battle_policy = policy
    try:
        d.fight(policy=policy)
    except Exception as exc:  # noqa: BLE001
        log.info("[moon] fight: %s", str(exc)[:120])
    finally:
        d.battle_policy = None
    for _ in range(4):
        if not d.scene_active():
            break
        d.advance_scene(40_000)
    d.close_menus()
    return "ball" if carrying else "flee"


def moon_cells(d) -> list:
    """Pacing targets on this map, WARP TILES EXCLUDED.

    Every step on a cave floor rolls for an encounter, so any spread of
    reachable cells will do -- except the ones that are doors. `pace_cells`
    hands back everything walkable, warps included, and `goto` standing ON a
    warp fires it: the first run walked itself out of MeteorFalls_B1F_1R and
    then spent its stall budget failing to path to a B1F_1R cell from
    MeteorFalls_1F_2R ("no-path from (14,27) to (15,12) on
    MeteorFalls_1F_2R") and gave up with 85 encounters banked and no stone.
    """
    cells = pace_cells(d)
    try:
        warps = {(int(e["x"]), int(e["y"]))
                 for e in d.exits(d.map_name())
                 if e.get("kind") == "warp"}
    except Exception:  # noqa: BLE001 - a map with no exit table has no doors
        warps = set()
    return [c for c in cells if c not in warps]


def hunt_moon_stone(d, budget: float, state_path=None) -> bool:
    """Pace a Meteor Falls room until a LUNATONE walks in carrying the stone.

    One Moon Stone per ~57 encounters in MeteorFalls_B1F_1R: LUNATONE owns
    35% of that room's land slots and `SetWildMonHeldItem` gives it `item2`
    on a `Random() % 100` of 95-99, i.e. 5% (pokemon_3.c:1320-1339). Measured
    on this line at roughly five seconds an encounter, so the budget is spent
    on RNG, not on walking.

    Nothing here can lose a carrier to EXPLOSION, which is why the deep room
    is safe to prefer: LUNATONE does not learn it until L49
    (level_up_learnsets.h:4662) and B1F_1R rolls L33-39.

    A wrong room is recovered from rather than fatal. Any Meteor Falls room
    in MOON_MAPS is a fine place to hunt, so falling through a door just
    re-anchors the target list; only leaving the cave entirely costs a walk
    back.
    """
    moon_id = d.consts.items["ITEM_MOON_STONE"]
    stone = item_name(d, "ITEM_MOON_STONE")
    if _has(d, stone):
        return True
    here = moon_map(d)
    if here not in MOON_MAPS:
        log.info("[moon] no Meteor Falls room reachable (at %s)", here)
        return False

    def anchor():
        """Adopt the current room: fresh grid, fresh targets, fresh table."""
        try:
            d.sync_grid()
        except Exception as exc:  # noqa: BLE001
            log.info("[moon] sync_grid: %s", str(exc)[:90])
        room = d.map_name()
        got = moon_cells(d)
        table = table_species(d, room)
        log.info("[moon] anchored on %s: %d pacing cells, table %s", room,
                 len(got), sorted(table))
        return room, got, table

    here, cells, allowed = anchor()
    if not cells:
        log.info("[moon] nothing walkable on %s from %s", here, d.pos())
        return False

    deadline = time.time() + budget
    seen: dict = {}
    encounters = 0
    reported = 0
    stalled = 0
    reanchors = 0
    i = 0
    while time.time() < deadline:
        if _has(d, stone):
            break
        if d.in_battle():
            moon_encounter(d, moon_id, seen, allowed)
            encounters += 1
            strip_moon_stone(d, moon_id)
        elif d.scene_active():
            d.advance_scene(40_000)
            d.close_menus()
        elif d.map_name() != here:
            # A door fired mid-pace. Any MOON_MAPS room will do; anywhere
            # else has to be walked back, because the encounter table is the
            # whole point of standing here.
            now = d.map_name()
            if now in MOON_MAPS:
                log.info("[moon] stepped from %s into %s -- hunting there "
                         "instead", here, now)
                here, cells, allowed = anchor()
            else:
                log.info("[moon] left the cave to %s -- walking back", now)
                if not reach(d, MOON_MAPS[0], budget=300.0) \
                        or d.map_name() not in MOON_MAPS:
                    log.info("[moon] could not get back in (at %s)",
                             d.map_name())
                    break
                here, cells, allowed = anchor()
            stalled = 0
        elif stalled >= 8:
            # NOT FATAL. Re-deriving the targets from where we actually
            # stand recovers a pocket the old list could not reach; only a
            # room that keeps refusing every target ends the hunt.
            reanchors += 1
            log.info("[moon] pacing stalled at %s (%s) -- re-anchor %d",
                     d.pos(), getattr(d, "last_goto_reason", "?"), reanchors)
            if reanchors > 12:
                log.info("[moon] %s keeps refusing every target -- stopping",
                         here)
                break
            here, cells, allowed = anchor()
            if not cells:
                break
            stalled = 0
        else:
            i += 1
            target = cells[(i * 7) % len(cells)]
            if target != d.pos():
                try:
                    # PER-CALL AND CLEARED. `Driver._journey_deadline`
                    # persists across calls, and an EXPIRED one makes
                    # `take_warp` refuse every approach cell for the rest of
                    # the run.
                    d._journey_deadline = min(deadline, time.time() + 45.0)
                    stalled = 0 if d.goto(*target, on_battle="raise") \
                        else stalled + 1
                except TravelInterrupted:
                    moon_encounter(d, moon_id, seen, allowed)
                    encounters += 1
                    strip_moon_stone(d, moon_id)
                    stalled = 0
                except Exception as exc:  # noqa: BLE001
                    log.info("[moon] walk: %s", str(exc)[:90])
                    stalled += 1
                finally:
                    d._journey_deadline = None
        # OUTSIDE EVERY BRANCH. The version this replaces logged the tally at
        # the foot of the loop but `continue`d out of the battle branch, so a
        # run that did nothing BUT fight printed nothing at all and looked
        # identical to a hang.
        if encounters >= reported + 10:
            reported = encounters
            log.info("[moon] %d encounters, %.0fs left, seen %s", encounters,
                     deadline - time.time(), seen)
            # Bank the walk, not just the stone: a re-run then starts in the
            # cave instead of paying for the trip again.
            if state_path:
                try:
                    d.save(state_path)
                except Exception as exc:  # noqa: BLE001
                    log.info("[moon] save: %s", str(exc)[:90])
    got = _has(d, stone)
    log.info("[moon] %d encounters, seen %s, %s held = %s", encounters, seen,
             stone, got)
    return got


def ensure_stone(d, const: str, budget: float, state_path=None) -> bool:
    """Put `const` in the bag, by whichever source the ROM actually offers.

    Trade first, and the trader's own table decides what is tradeable --
    `shard_trade.find_colour` matches against `COLOURS`, which is the four
    pairs the Route 124 script really offers. A stone that is not there has
    no repeatable source and falls through to a hunt.
    """
    stone = item_name(d, const)
    if _has(d, stone):
        log.info("[%s] already in the bag", stone)
        return True
    if find_colour(d, const) is not None:
        log.info("[%s] not held -- trading a shard for it", stone)
        return bool(trade(d, const))
    if const == "ITEM_MOON_STONE":
        return hunt_moon_stone(d, budget, state_path=state_path)
    log.info("[%s] no source known", stone)
    return False


def run_leg(d, leg: str, stone_budget: float, state_path=None) -> bool:
    pre, post, const = LEGS[leg]
    if registered(d, post):
        log.info("=== %s: already registered, skipping ===", post)
        return True
    stone = item_name(d, const)
    log.info("=== %s: %s + %s ===", post, pre, stone)

    if not owns(d, pre):
        # Not caught here: both pre-evolutions are already on this line, so
        # an absence means something else consumed them and a catch plan
        # would be a different script's job.
        log.info("[%s] no %s in the party or the boxes -- cannot evolve one",
                 leg, pre)
        return False

    # The slot comes first, and before the stone: a caught LUNATONE has to
    # arrive IN THE PARTY or it takes the Moon Stone into the box with it,
    # and `take_from_mon` only reaches a party member.
    if not free_slot(d, CENTERS[leg]):
        log.info("[%s] could not free a party slot", leg)
        return False

    if not ensure_stone(d, const, stone_budget, state_path=state_path):
        log.info("[%s] no %s obtained", leg, stone)
        return False
    log.info("[%s] *** %s IN THE BAG ***", leg, stone)

    # A caught LUNATONE filled the slot back up; the stone is off it by now,
    # so it is the cheapest thing in the party to box.
    if not free_slot(d, CENTERS[leg]):
        log.info("[%s] stone held but no slot to withdraw %s into", leg, pre)
        return False

    # STAND IN THE CENTER BEFORE `spend`. It withdraws the boxed
    # pre-evolution, and its first move is `share_grind.to_center`, which
    # only short-circuits when the map already ends in `PokemonCenter_1F`;
    # reached from anywhere else it walks into `heal_at_nearest_center` and
    # the 150-second `nav.route_legs` wedge documented on `to_pc`. The stone
    # was just fetched from a trader's house or a cave, so this is never a
    # no-op by luck.
    if not to_pc(d, CENTERS[leg]):
        log.info("[%s] %s held but no Center to withdraw %s at", leg, stone,
                 pre)
        return False

    rc = spend(d, stone, pre, post)
    ok = registered(d, post)
    log.info("=== %s: spend rc=%s registered=%s ===", post, rc, ok)
    return ok


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True,
                    help="a FORK; written in place after every leg")
    ap.add_argument("--only", default=",".join(ORDER))
    # 1200s PER STONE, NOT MORE, and `chain.py` is the reason. It registers
    # this leg with NO extra argv ("stones2": ("scripts/stones2.py", []))
    # and kills a leg at `--leg-timeout`, default 2400s -- so a default that
    # let one hunt run 5400s would be killed mid-cave every time, banking
    # nothing but the ten-encounter checkpoints. 1200s is ~240 encounters at
    # the measured five seconds each, i.e. ~84 LUNATONE at a 35% share, and
    # 0.95**84 is a 1.4% chance of not seeing a single carrier. The trip in
    # and the two evolutions fit in the rest of the window.
    ap.add_argument("--stone-budget", type=float, default=1200.0,
                    help="seconds for ONE stone; the LUNATONE hunt needs it")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    if "line3" in a.state or "milestone" in a.state:
        raise SystemExit(f"refusing to drive {a.state}: fork it first")

    legs = [s.strip().lower() for s in a.only.split(",") if s.strip()]
    bad = [s for s in legs if s not in LEGS]
    if bad:
        raise SystemExit(f"unknown legs {bad}; known {sorted(LEGS)}")

    d = Driver(a.state)
    to_overworld(d)
    d.advance_scene(40_000)
    before = dex_count(d)
    log.info("booted %s %s | dex %d | items %s", d.map_name(), d.pos(),
             before, (d.state.bag().get("items") or {}))

    # SURF ONCE, FOR THE WHOLE RUN. `nav.surfing` is off by default and nav
    # counts water as wall (shard_trade.py:238-240), and both legs cross it:
    # the Yellow Shard ball sits in a Route124 reef, and the deep Meteor
    # Falls rooms -- the only ones where LUNATONE is 35% of encounters rather
    # than 20% -- are behind water and a waterfall.
    log.info("surf routing enabled: %s | surf=%s waterfall=%s dive=%s",
             _enable_surf(d), d.can_surf(), d.can_waterfall(), d.can_dive())

    # NO STARTING MAP IS ASSUMED, and underwater is the one that has to be
    # handled here rather than left to the nav layer: every route this leg
    # takes begins with a flight, and Fly is refused outright down there.
    if d.underwater() and not surface(d):
        log.info("still underwater at %s %s -- every leg here starts with a "
                 "flight, which MAP_TYPE_UNDERWATER refuses", d.map_name(),
                 d.pos())
        return 1

    done = {}
    for leg in legs:
        try:
            done[leg] = run_leg(d, leg, a.stone_budget, state_path=a.state)
        except Exception as exc:  # noqa: BLE001 - one leg never kills the rest
            log.exception("[%s] raised: %s", leg, str(exc)[:200])
            done[leg] = False
        # Bank after EVERY leg, not just at the end: a crash in the second
        # leg must not cost the first one's species.
        try:
            d.save(a.state)
            log.info("[%s] saved %s", leg, a.state)
        except Exception as exc:  # noqa: BLE001
            log.info("save: %s", str(exc)[:120])

    after = dex_count(d)
    log.info("dex %d -> %d | %s | stones held: %s", before, after, done,
             {item_name(d, c): _has(d, item_name(d, c))
              for _p, _q, c in LEGS.values()})
    return 0 if all(done.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
