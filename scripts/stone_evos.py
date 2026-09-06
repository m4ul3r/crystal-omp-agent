#!/usr/bin/env python
"""Three stone evolutions on one save: NINETALES, STARMIE, DELCATTY.

Each leg is the same three questions -- do we own the pre-evolution, do we
own the stone, has the stone been spent -- and the answers are ROM facts,
not lore:

    ITEM_FIRE_STONE  + VULPIX -> NINETALES   (evolution.h:28)
    ITEM_WATER_STONE + STARYU -> STARMIE     (evolution.h:76)
    ITEM_MOON_STONE  + SKITTY -> DELCATTY    (evolution.h:151)

WHERE THE STONES COME FROM, checked against the cartridge rather than a
walkthrough. Lilycove's department store does NOT sell them: all four 5F
clerks run `pokemartdecoration2`, whose list entries are DECOR_ ids, and the
ids that read as ITEM_SUN_STONE..ITEM_LEAF_STONE (93-98) are
DECOR_BALTOY_DOLL..DECOR_BLASTOISE_DOLL by number
(LilycoveCity_DepartmentStore_5F/scripts.inc:14-27 vs
include/constants/decorations.h:93-98). So:

* FIRE and WATER are repeatable: the Route 124 treasure hunter swaps one
  coloured shard for one stone, forever (scripts/shard_trade.py). Cost is a
  shard, never money. RED->FIRE, BLUE->WATER.
* MOON has exactly ONE ground item in the whole game --
  `MeteorFalls_1F_1R_EventScript_1B1815: finditem ITEM_MOON_STONE`
  (data/item_ball_scripts.inc:305-307) -- and `FLAG_ITEM_METEOR_FALLS_1F_1R_3`
  is ALREADY SET in this line, with no hidden Moon Stone anywhere in
  data/maps/*/map.json. The only Moon Stone this save can still reach is the
  one a wild LUNATONE carries: base_stats gives it `item2 = ITEM_MOON_STONE`
  and no item1, i.e. the 5% slot. So the Moon Stone leg is a hunt: read the
  enemy's `gBattleMons[1].item` (plaintext, tactics.py:316), flee everything
  that is not carrying one, throw a ball at the one that is, and take the
  stone off the mon that arrives.

Missing pre-evolutions are caught here too, because a stone with nothing to
spend it on is not progress:

* STARYU is Lilycove's super-rod 15% slot (wild_encounters slot 7).
* SKITTY is Route 116 land slots 10-11, i.e. 1%+1%.

Usage (one leg per command, in place on the state given):

    scripts/stone_evos.py --state saves/stone-out.state --only ninetales
    scripts/stone_evos.py --state saves/stone-out.state --only starmie
    scripts/stone_evos.py --state saves/stone-out.state --only delcatty
"""
import argparse
import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from pokeagent.trek import Driver, TravelInterrupted  # noqa: E402
from pokeagent.dex import DexTarget  # noqa: E402
from pokeagent.teaching import Teacher  # noqa: E402

# Proven machinery, imported rather than re-derived. `shard_trade` owns the
# shard->stone trade (including the dive into the sealed lagoon) and the
# withdraw-and-spend; `stone_evos_hunt` is a frozen copy of the rod hunter,
# frozen because its author is editing the original in a sibling session.
from shard_trade import (  # noqa: E402
    _has, _travel, item_name, make_room, spend, to_center, to_overworld,
    trade,
)
from stone_evos_hunt import Hunt  # noqa: E402

log = logging.getLogger("stone_evos")

BOX_SIZE = 30

#: leg -> (pre-evolution, post-evolution, stone constant)
LEGS = {
    "ninetales": ("VULPIX", "NINETALES", "ITEM_FIRE_STONE"),
    "starmie": ("STARYU", "STARMIE", "ITEM_WATER_STONE"),
    "delcatty": ("SKITTY", "DELCATTY", "ITEM_MOON_STONE"),
}

#: The shard trader's four pairs (Route124_DivingTreasureHuntersHouse
#: scripts.inc:225-243). A stone that is not here has no repeatable source.
TRADEABLE = {
    "ITEM_FIRE_STONE", "ITEM_THUNDER_STONE", "ITEM_WATER_STONE",
    "ITEM_LEAF_STONE",
}

#: Where a missing pre-evolution is caught: (map, terrain-or-rod).
CATCH = {
    "STARYU": ("LilycoveCity", "super_rod"),
    "SKITTY": ("Route116", "grass"),
}

#: Meteor Falls rooms, RICHEST LUNATONE SHARE FIRST. Land slots 3,4,5,7 are
#: Lunatone in the deeper rooms (10+10+10+5 = 35% of encounters) against
#: slots 5,6,7 in the entrance room (10+5+5 = 20%). Level does not enter the
#: Gen 3 catch formula at all, so the L33-39 rooms cost nothing extra.
MOON_MAPS = ("MeteorFalls_B1F_1R", "MeteorFalls_1F_2R",
             "MeteorFalls_1F_1R")
BALL = "ULTRA BALL"

#: A route or cave is NOT a Fly destination, so the flight goes to a town the
#: target CONNECTS TO and only the last leg is walked. Left to itself,
#: `travel` walked from Oldale's Pokemon Center towards Route116 -- five maps
#: of pathing for somewhere one hop from a Fly stop.
#:   Route116 connects left to RustboroCity and down to VerdanturfTown
#:   (Route116/map.json connections); MeteorFalls_1F_1R's own warp 0 is
#:   Route114 (8,63), and Route114 connects right to FallarborTown.
GATEWAYS = {
    "Route116": ("RustboroCity", "VerdanturfTown"),
    "MeteorFalls_1F_1R": ("FallarborTown", "RustboroCity"),
    "MeteorFalls_1F_2R": ("FallarborTown", "RustboroCity"),
    "MeteorFalls_B1F_1R": ("FallarborTown", "RustboroCity"),
}


def reach(d, map_name: str, budget: float = 420.0) -> bool:
    """Fly as close as the region map allows, then walk the rest."""
    if d.map_name() == map_name:
        return True
    try:
        if not d.flight.flyable_here():
            d.flight.step_outside()
        if d.fly_to(map_name):
            log.info("  flew to %s", d.map_name())
            return True
    except Exception as exc:  # noqa: BLE001
        log.info("  fly %s: %s", map_name, str(exc)[:90])
    for town in GATEWAYS.get(map_name, ()):
        if d.map_name() == town:
            break
        try:
            if not d.flight.flyable_here():
                d.flight.step_outside()
            if d.fly_to(town):
                log.info("  flew to %s as the gateway to %s", d.map_name(),
                         map_name)
                break
        except Exception as exc:  # noqa: BLE001
            log.info("  fly %s: %s", town, str(exc)[:90])
    return _travel(d, map_name, budget)


def species_id(d, name: str) -> int:
    """Numeric species for a display name, off the cartridge's name table."""
    want = name.upper()
    table = getattr(d.consts, "species", None)
    if isinstance(table, dict):
        hit = table.get(f"SPECIES_{want}")
        if hit:
            return hit
    for sid in range(1, 412):
        try:
            if d.names.species(sid).upper() == want:
                return sid
        except Exception:  # noqa: BLE001
            continue
    raise SystemExit(f"no species named {name!r} in this ROM")


def dex_view(d):
    return DexTarget(d.emu, d.names, d.consts, d.nav, spec=d.spec)


def registered(d, species: str) -> bool:
    """Is `species` CAUGHT in the dex right now?

    Via natdex ids, because `DexTarget.missing()` yields ENTRY OBJECTS while
    `Collector.missing()` yields species ids -- comparing across the two is
    silently always-False.
    """
    t = dex_view(d)
    caught, _seen = t.dex_flags(d.state)
    sid = species_id(d, species)
    entry = t.by_species.get(sid)
    return bool(entry and entry.natdex in caught)


def dex_count(d) -> int:
    import re

    m = re.search(r"dex (\d+)/", dex_view(d).summary(d.state))
    return int(m.group(1)) if m else -1


def owns(d, species: str):
    """`("party", mon)`, `("box", flat_slot)` or None."""
    want = species.upper()
    for m in d.state.party():
        if m.is_egg:
            continue
        try:
            if d.names.species(m.species).upper() == want:
                return ("party", m)
        except Exception:  # noqa: BLE001
            continue
    for slot, b in dex_view(d).boxed():
        try:
            if d.names.species(b.species).upper() == want:
                return ("box", slot)
        except Exception:  # noqa: BLE001
            continue
    return None


# ---- catching a missing pre-evolution ---------------------------------


def collector(d):
    """One `Collector` per driver, reused.

    It is the proven walker and the proven shopper, but each instance starts
    a StallWatch thread and re-points the live feed, so building a second
    one per leg is not free.
    """
    from collect import Collector

    got = getattr(d, "_stone_collector", None)
    if got is None:
        feed = getattr(getattr(d, "feed", None), "name", None)
        got = Collector(d, feed_name=feed)
        d._stone_collector = got
    return got


def balls_held(d) -> int:
    return sum((d.state.bag().get("poke_balls") or {}).values())


def stock_balls(d, want: int = 40) -> int:
    """Buy ULTRA BALLs, because the Moon Stone hunt is paid for in balls.

    Gen 3 catch odds do not look at level, only at the HP fraction, the
    species' catch rate and the ball: a full-HP LUNATONE (catch rate 45) is
    about a one-in-nine ULTRA BALL each throw, so one holder costs ~9 balls
    and this save starts with 11. `Collector.restock_balls` is not used
    because it deliberately shops for the CHEAPEST ball (collect.py:818-833)
    and flies to a basic-tier Mart to do it; here the throw count matters
    more than the price, and the money is 999,999.
    """
    have = balls_held(d)
    if have >= want:
        return have
    from pokeagent.mart import Mart

    c = collector(d)
    mart = "MossdeepCity_Mart"       # a Fly town whose shelf carries ULTRA
    if not c.goto_map(mart, budget=300.0):
        log.info("[balls] could not reach %s (%s)", mart, d.last_goto_reason)
        return have
    cell = c.clerk_cell(mart)
    if cell is None:
        log.info("[balls] no clerk on %s", mart)
        return have
    try:
        d.talk_to(*cell)
    except Exception as exc:  # noqa: BLE001
        log.info("[balls] clerk: %s", str(exc)[:90])
        return have
    d.settle(120)
    m = Mart(d)
    for _ in range(4):
        if m.is_open():
            break
        d.emu.run_sequence("A:4 .:40")
    if not m.is_open():
        log.info("[balls] the clerk did not open a shop")
        d.emu.run_sequence("B:4 .:20 B:4 .:20")
        return have
    qty = want - have
    ok = m.buy(BALL, qty)
    log.info("[balls] buy %dx %s -> %s (%s)", qty, BALL, ok,
             getattr(m, "last_reason", ""))
    # B only: a blind A in a shop list BUYS, and the description box has to
    # be closed before anything is saved.
    for _ in range(12):
        if not d.scene_active() and not m.is_open():
            break
        d.emu.run_sequence("B:4 .:24")
    d.advance_scene(40_000)
    now = balls_held(d)
    log.info("[balls] %d -> %d", have, now)
    return now


def catch_fishing(d, hunt, species: str, casts: int, budget: float) -> bool:
    """Delegate to the rod hunter's own leg.

    `Hunt.hunt` takes `[(map, fly_landing)]`, not `[map]`: which landing the
    flight uses decides whether `travel` finds a sea road at all, and
    Lilycove is its own landing.
    """
    sid = species_id(d, species)
    map_name, _rod = CATCH[species]
    return bool(hunt.hunt(sid, species, [(map_name, map_name)],
                          max_casts=casts, budget_s=budget))


def catch_pacing(d, hunt, species: str, budget: float) -> bool:
    """Walk a map's encounter terrain until `species` shows up and is caught.

    `Collector.pace_map` is the proven walker (it jumps around the patch
    instead of shuffling on one cell, and it hands encounters to a
    CATCH-aware fight), so it is reused whole; only the arrival is ours,
    since a Collector would otherwise re-plan the entire dex.
    """
    map_name, terrain = CATCH[species]
    if not reach(d, map_name):
        log.info("[%s] could not reach %s (at %s)", species, map_name,
                 d.map_name())
        return False
    try:
        log.info("[%s] sync_grid(%s): %d cells", species, map_name,
                 d.sync_grid())
    except Exception as exc:  # noqa: BLE001
        log.info("[%s] sync_grid: %s", species, str(exc)[:90])
    c = collector(d)
    deadline = time.time() + budget
    while time.time() < deadline:
        c.pace_map(deadline, terrain=terrain)
        if owns(d, species):
            log.info("[%s] CAUGHT", species)
            return True
        if d.map_name() != map_name and not reach(d, map_name):
            break
    return bool(owns(d, species))


def ensure_mon(d, hunt, species: str, budget: float) -> bool:
    where = owns(d, species)
    if where:
        log.info("[%s] already owned (%s)", species, where[0])
        return True
    if species not in CATCH:
        log.info("[%s] not owned and no catch plan", species)
        return False
    _map, how = CATCH[species]
    log.info("[%s] not owned -- hunting on %s (%s)", species, _map, how)
    if how == "super_rod":
        return catch_fishing(d, hunt, species, casts=300, budget=budget)
    return catch_pacing(d, hunt, species, budget)


# ---- the Moon Stone hunt ----------------------------------------------


def pace_cells(d, limit: int = 120) -> list:
    """Reachable cells on this map, spread out, nearest first.

    Meteor Falls is a CAVE: there is no grass, so `terrain_cells("grass")`
    (collect.py:439) answers nothing and the collector's walker cannot hunt
    here at all. Every step on a cave floor rolls for an encounter, so any
    two reachable cells far enough apart will do.
    """
    here = d.map_name()
    try:
        reach = list(d.nav.reachable(here, d.pos(), d.elevation()))
    except Exception as exc:  # noqa: BLE001
        log.info("[moon] reachable(%s): %s", here, str(exc)[:90])
        return []
    walkable = []
    for (x, y) in reach:
        c = d.nav.cell(here, x, y)
        if c is None or d.nav._is_water(c):
            continue
        walkable.append((x, y))
    px, py = d.pos()
    walkable.sort(key=lambda c: abs(c[0] - px) + abs(c[1] - py))
    return walkable[:limit]


def settled_enemy(d, frames: int = 2400):
    """`gBattleMons[1]` ONLY once it belongs to THIS battle.

    MEASURED by peers and confirmed as a live bug: the enemy block is stale
    right after `state.battle_ready()` goes true -- it can still hold the
    PREVIOUS battle's mon, so a decision taken there is taken about the
    wrong Pokemon. Two cheap gates, the same pair the rod hunter uses: the
    action menu is up (the intro has finished and a policy is about to be
    asked anyway) and the species is one THIS MAP's table can roll.
    """
    allowed = set()
    try:
        rows = dex_view(d).wild.for_map(d.map_name())
        allowed = {d.names.species(r.species) for r in rows}
    except Exception:  # noqa: BLE001 - not every map has a table
        pass
    best = None
    spent = 0
    while spent < frames and d.in_battle():
        if d.battle.at_action_menu():
            try:
                enemy = d.battle.battler(1)
            except Exception as exc:  # noqa: BLE001
                log.info("[moon] battler read: %s", str(exc)[:90])
                enemy = None
            best = enemy or best
            name = getattr(enemy, "name", None)
            if name and (not allowed or name in allowed):
                return enemy
        # TICKING WILL NEVER GET YOU A MENU. The action menu is
        # controller-driven and the turn's opening text blocks it until an A
        # press -- battle.py:1308-1332 says so, and provides
        # `await_action_menu()` for exactly this. Ticking instead meant this
        # loop never saw a menu, returned None, and the caller counted an
        # encounter and `continue`d past its own tally log: measured spinning
        # on ONE battle for 30 minutes with the feed frozen on "Wild LUNATONE
        # appeared!", zero output, burning the whole stone budget. The shard
        # legs (NINETALES/STARMIE) never fight, which is why this looked
        # proven.
        if not d.battle.await_action_menu():
            d.emu.tick(20)
        spent += 20
    if not d.in_battle():
        return None
    return best


def moon_encounter(d, moon_id: int, seen: dict) -> str:
    """Play one wild encounter. Ball it only if it is CARRYING a Moon Stone.

    `gBattleMons` is plaintext (the Gen 3 substructure encryption covers only
    the boxed struct), so the enemy's held item is readable before any
    decision is made -- which turns a 5% drop into a 5% *inspection* and
    costs no ball at all on the other 95%.
    """
    enemy = settled_enemy(d)
    if enemy is None:
        return "none"
    name = getattr(enemy, "name", "?")
    item = int(getattr(enemy, "item", 0) or 0)
    seen[name] = seen.get(name, 0) + 1
    carrying = item == moon_id
    if carrying:
        log.info("[moon] *** %s is holding %s -- throwing %s ***", name,
                 d.names.item(item), BALL)
        policy = lambda _f: ("ball", BALL)  # noqa: E731
    else:
        policy = lambda _f: "flee"  # noqa: E731
    # `battle_policy` is the hook the walker itself consults (trek.py:3159);
    # `encounter_policy` is Crystal's API and has no consumer here. Setting
    # it as well as passing `policy=` means a battle that starts inside a
    # `goto` leg is decided the same way.
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


def strip_moon_stone(d, moon_id: int) -> bool:
    """Take the Moon Stone off whichever party member walked in with it."""
    t = Teacher(d)
    for i, m in enumerate(d.state.party()):
        held = int(getattr(m, "item", 0) or getattr(m, "held_item", 0) or 0)
        if held != moon_id:
            continue
        log.info("[moon] taking %s off party slot %d (%s)",
                 d.names.item(moon_id), i, m.nickname)
        if t.take_from_mon(i):
            return _has(d, item_name(d, "ITEM_MOON_STONE"))
        log.info("[moon] take_from_mon refused: %s",
                 getattr(t, "last_reason", "?"))
    return _has(d, item_name(d, "ITEM_MOON_STONE"))


def moon_map(d) -> str:
    """The Meteor Falls room with the RICHEST Lunatone share we can enter.

    Land slot weights in Gen 3 are {20,20,10,10,10,10,5,5,4,4,1,1}, and the
    tables (wild_encounters.json, Sapphire group) put Lunatone in slots
    3,4,5,7 of the deeper rooms -- 35% of encounters -- against slots 5,6,7
    of the entrance room, 20%. At a 5% hold rate that is one Moon Stone per
    57 encounters instead of one per 100, which is the difference between
    half an hour and an hour.
    """
    for name in MOON_MAPS:
        if d.map_name() == name or reach(d, name, budget=240.0):
            return d.map_name()
    return d.map_name()


#: The Mossdeep Center, next door to the Mart the balls come from.
MOON_CENTER = "MossdeepCity_PokemonCenter_1F"


def free_party_slot(d, center: str = MOON_CENTER) -> bool:
    """Bank one party member so a caught Lunatone arrives IN THE PARTY.

    `share_grind.to_center` is deliberately NOT used: it calls
    `heal_at_nearest_center`, which asks `nav.route_legs` for a route out of
    an INDOOR map, and from inside MossdeepCity_Mart that spent 150 seconds
    in `nav._crossings` without ticking the core at all -- the StallWatch
    caught it as "wedged at MossdeepCity_Mart (3,3) for 150s ... the process
    is blocked in Python" (pokeagent/nav.py:1212). `Collector.goto_map`
    flies to the landing and walks in instead, which is the same trip
    without the whole-region pathfind.

    The victim must be carrying NOTHING: a mon deposited while holding an
    item takes the item into the box with it, and one of these is holding
    another agent's EXP. SHARE.
    """
    from pokeagent.storage import Storage

    party = [m for m in d.state.party() if not m.is_egg]
    if len(party) < 6:
        return True
    cands = [(i, m) for i, m in enumerate(d.state.party())
             if not m.is_egg and not m.held_item]
    if not cands:
        log.info("[moon] every party member holds an item -- not stripping "
                 "one")
        return False
    i, victim = min(cands, key=lambda p: p[1].level or 0)
    if not d.map_name().endswith("PokemonCenter_1F"):
        if not collector(d).goto_map(center, budget=300.0):
            log.info("[moon] could not reach %s (%s)", center,
                     d.last_goto_reason)
            return False
    st = Storage(d)
    if not st.pc_cells():
        log.info("[moon] no PC on %s", d.map_name())
        return False
    log.info("[moon] depositing %s L%s (holds nothing) to free a slot",
             victim.nickname, victim.level)
    ok = st.deposit(i)
    st.close()
    if not ok:
        log.info("[moon] deposit refused: %s", getattr(st, "last_reason", "?"))
    return ok


def hunt_moon_stone(d, hunt, budget: float) -> bool:
    moon_id = d.consts.items["ITEM_MOON_STONE"]
    stone = item_name(d, "ITEM_MOON_STONE")
    if _has(d, stone):
        return True
    # A party slot and the balls first, both in Mossdeep: walking back out of
    # Meteor Falls to fetch either would cost the hunt twice.
    if not free_party_slot(d):
        log.info("[moon] no free party slot -- a caught Lunatone would box "
                 "its own Moon Stone")
    stock_balls(d, want=40)
    here = moon_map(d)
    if here not in MOON_MAPS:
        log.info("[moon] no Meteor Falls room reachable (at %s)", here)
        return False
    log.info("[moon] hunting on %s", here)
    try:
        d.sync_grid()
    except Exception:  # noqa: BLE001
        pass
    cells = pace_cells(d)
    if not cells:
        log.info("[moon] nothing walkable on %s from %s", here, d.pos())
        return False
    log.info("[moon] %d walkable cells on %s, hunting LUNATONE for its 5%% "
             "Moon Stone", len(cells), here)
    deadline = time.time() + budget
    seen: dict = {}
    encounters = 0
    i = 0
    stalled = 0
    while time.time() < deadline:
        if _has(d, stone):
            break
        if d.in_battle():
            moon_encounter(d, moon_id, seen)
            encounters += 1
            strip_moon_stone(d, moon_id)
            continue
        if d.scene_active():
            d.advance_scene(40_000)
            d.close_menus()
        if stalled >= 8:
            log.info("[moon] pacing stalled: %s", d.last_goto_reason)
            break
        i += 1
        target = cells[(i * 7) % len(cells)]
        if target == d.pos():
            continue
        try:
            # PER-CALL, AND CLEARED. `Driver._journey_deadline` persists
            # across calls, and an EXPIRED one makes `take_warp` refuse
            # every approach cell afterwards -- so a walk budget left behind
            # here would seal the next warp this run tries to use.
            d._journey_deadline = min(deadline, time.time() + 45.0)
            if d.goto(*target, on_battle="raise"):
                stalled = 0
            else:
                stalled += 1
        except TravelInterrupted:
            moon_encounter(d, moon_id, seen)
            encounters += 1
            strip_moon_stone(d, moon_id)
            stalled = 0
        except Exception as exc:  # noqa: BLE001
            log.info("[moon] walk: %s", str(exc)[:90])
            stalled += 1
        finally:
            d._journey_deadline = None
        if encounters and encounters % 10 == 0:
            log.info("[moon] %d encounters so far: %s", encounters, seen)
    log.info("[moon] %d encounters, table %s, stone held = %s", encounters,
             seen, _has(d, stone))
    return _has(d, stone)


def ensure_stone(d, hunt, const: str, budget: float) -> bool:
    stone = item_name(d, const)
    if _has(d, stone):
        log.info("[%s] already in the bag", stone)
        return True
    if const in TRADEABLE:
        log.info("[%s] not held -- trading a shard for it", stone)
        return bool(trade(d, const))
    if const == "ITEM_MOON_STONE":
        return hunt_moon_stone(d, hunt, budget)
    log.info("[%s] no source known", stone)
    return False


# ---- a leg ------------------------------------------------------------


def run_leg(d, hunt, leg: str, mon_budget: float, stone_budget: float,
            stone_only: bool = False) -> bool:
    pre, post, const = LEGS[leg]
    if registered(d, post):
        log.info("=== %s: already registered ===", post)
        return True
    log.info("=== %s: %s + %s ===", post, pre, item_name(d, const))
    if stone_only:
        # Bank the stone and stop. The pre-evolution is normally acquired
        # FIRST -- a stone with nothing to spend it on is not progress -- but
        # the two hunts are independent and this lets the slow one run alone.
        return ensure_stone(d, hunt, const, stone_budget)
    if not ensure_mon(d, hunt, pre, mon_budget):
        return False
    if not ensure_stone(d, hunt, const, stone_budget):
        return False
    rc = spend(d, item_name(d, const), pre, post)
    ok = registered(d, post)
    log.info("=== %s: spend rc=%s registered=%s ===", post, rc, ok)
    return ok


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True,
                    help="a FORK; written in place after every leg")
    ap.add_argument("--only", default="ninetales,starmie,delcatty")
    ap.add_argument("--mon-budget", type=float, default=2400.0)
    ap.add_argument("--stone-budget", type=float, default=3600.0)
    ap.add_argument("--stone-only", action="store_true",
                    help="acquire the stone and stop, skipping the catch")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(message)s")
    if "line3" in a.state or "milestone" in a.state:
        raise SystemExit(f"refusing to drive {a.state}: fork it first")

    legs = [s.strip().lower() for s in a.only.split(",") if s.strip()]
    bad = [s for s in legs if s not in LEGS]
    if bad:
        raise SystemExit(f"unknown legs {bad}; known {sorted(LEGS)}")

    d = Driver(a.state)
    to_overworld(d)
    d.advance_scene(40_000)
    hunt = Hunt(d)
    before = dex_count(d)
    log.info("booted %s %s | dex %d | items %s", d.map_name(), d.pos(),
             before, (d.state.bag().get("items") or {}))

    done = {}
    for leg in legs:
        try:
            done[leg] = run_leg(d, hunt, leg, a.mon_budget, a.stone_budget,
                                stone_only=a.stone_only)
        except Exception as exc:  # noqa: BLE001 - one leg never kills the rest
            log.exception("[%s] raised: %s", leg, str(exc)[:200])
            done[leg] = False
        try:
            hunt.save(a.state)
        except Exception as exc:  # noqa: BLE001
            log.info("save: %s", str(exc)[:120])
    log.info("dex %d -> %d | %s", before, dex_count(d), done)
    log.info("%s", dex_view(d).summary(d.state))
    return 0 if all(done.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
