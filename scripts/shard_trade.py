#!/usr/bin/env python
"""Trade a shard for an evolution stone at the Route 124 treasure hunter.

Sapphire sells no stones, so every OTHER stone in the game is a single ground
item and every use is one-shot. This man is the exception: an UNLIMITED,
repeatable source, with no gating flag anywhere in his script.

The cartridge, not a walkthrough:

* The house is `Route124_DivingTreasureHuntersHouse`; the man is the only
  object_event, `OBJ_EVENT_GFX_MAN_6` at (5,4)
  (pret/data/maps/Route124_DivingTreasureHuntersHouse/map.json:13-27). Its
  two warps both land on Route124 warp 0, which is Route124 (70,48)
  (map.json:28-43, pret/data/maps/Route124/map.json warp_events).
* The trade table is four pairs, one shard each:
  RED->FIRE, YELLOW->THUNDER, BLUE->WATER, GREEN->LEAF
  (scripts.inc:225-243). The body removes one shard and gives one stone
  (`removeitem`/`giveitem`, :260-262) and then re-offers while any shard is
  left (:264-269), so this is a loop, not a one-shot.
* THE OPTION ORDER IS A BITMASK, and that is the whole reliability story.
  `EventScript_163E44` (:30-44) sets VAR_TEMP_1 from `checkitem` on each
  colour: RED=1, YELLOW=2, BLUE=4, GREEN=8. `EventScript_163EB0` (:67-86)
  switches on the mask into fifteen different `multichoice` lists. Reading
  every case (:88-223) the order is invariant -- RED, then YELLOW, then BLUE,
  then GREEN, filtered to the colours actually in the bag, with CANCEL last.
  So the index of a colour is simply how many LOWER-ranked colours you are
  carrying, and carrying exactly one makes it 0. Confirmed against the
  cartridge's own table: MultichoiceList_61 (mask 4, BLUE only) is
  `{OtherText_BlueShard, gOtherText_CancelNoTerminator}`
  (pret/src/script_menu.c:383-387) -- option 0 is the trade.

That is why this script computes the index from the bag instead of hardcoding
it, and logs the ROM's decoded labels next to the index it picked.

ROUTE 124 IS A ROCK MAZE, NOT AN OPEN SEA, and that is the other half of the
job. Three of the four shard balls -- Blue (31,53), Yellow (58,11), Red
(28,12) -- sit in lagoons whose boundary is solid reef and which touch no map
edge, so `travel` and `goto` are both right to say "no-path". The way in is
the one the house's own name advertises: Route124 has a `dive` seam to
Underwater1, and the seafloor tunnels pass under the reef. `dive_plan`
computes the (dive tile, surfacing tile) pair from nav rather than tabulating
it, and `_ensure_diver` withdraws a boxed DIVE knower -- preferring the
stone's own target, because LOMBRE learns DIVE and so opens the lagoon it is
waiting to be evolved in.

Then it spends the stone itself. `scripts/stone_evolve.py` cannot: its pair
table spells the item "THUNDER STONE" and the bag holds `THUNDERSTONE`, so it
refuses a stone that is in the ITEMS pocket. Every item name here is resolved
off the cartridge.

    scripts/shard_trade.py --state saves/mine.state --want "WATER STONE"
    scripts/shard_trade.py --state saves/mine.state --want "THUNDERSTONE"
"""
import argparse
import logging
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from pokeagent.trek import Driver, TravelError, TravelInterrupted  # noqa: E402
from pokeagent.dex import DexTarget  # noqa: E402
from pokeagent.menus import Menus  # noqa: E402
from pokeagent.storage import Storage  # noqa: E402
from pokeagent.teaching import Teacher  # noqa: E402
from share_grind import to_center, unwedge  # noqa: E402

log = logging.getLogger("shard_trade")

#: One row per colour, IN THE MULTICHOICE'S OWN ORDER (RED, YELLOW, BLUE,
#: GREEN -- scripts.inc:88-223), so a row's position IS its rank. Each ball
#: is an object_event and therefore blocks its own cell.
#:
#: THE ITEMS ARE ROM CONSTANTS, NOT DISPLAY STRINGS, and that is a bug fix
#: rather than tidiness. The trade for a Yellow Shard SUCCEEDED and the script
#: called it a failure, because the item the bag actually holds is
#: `THUNDERSTONE` -- one word -- while every table in this repo had written
#: "THUNDER STONE". Same trap as share_grind's "EXP. SHARE" (share_grind.py:36).
#: The display name is asked of the cartridge instead of guessed.
#:
#: shard const, stone const, mask bit, ball map, ball x, ball y, ROM
#: multichoice id when that colour is the only one held.
COLOURS = (
    ("ITEM_RED_SHARD",    "ITEM_FIRE_STONE",    1, "Route124", 28, 12, 58),
    ("ITEM_YELLOW_SHARD", "ITEM_THUNDER_STONE", 2, "Route124", 58, 11, 59),
    ("ITEM_BLUE_SHARD",   "ITEM_WATER_STONE",   4, "Route124", 31, 53, 61),
    ("ITEM_GREEN_SHARD",  "ITEM_LEAF_STONE",    8, "Route126", 14,  1, 65),
)

#: The man, and the Route124-side warp that reaches his house.
HUNTER = (5, 4)
HOUSE = "Route124_DivingTreasureHuntersHouse"
HOUSE_WARP = ("Route124", 70, 48)

#: A storage box holds thirty; `DexTarget.boxed` numbers slots flat across all
#: boxes, the same convention stone_evolve.py uses.
BOX_SIZE = 30


def item_name(d, const: str) -> str:
    """The name the BAG will show for a ROM item constant.

    `d.consts.items` is the generated `ITEM_*` enum and `d.names.item` decodes
    `gItems[].name` off the cartridge, so this is the string the parsed bag
    snapshot is keyed by -- `ITEM_THUNDER_STONE` resolves to `THUNDERSTONE`,
    which is exactly the mismatch that made a successful trade read as a
    failure.
    """
    return d.names.item(d.consts.items[const])


def _squash(text: str) -> str:
    """Compare item names ignoring spaces, dots and case.

    Lets a caller write `--want "THUNDER STONE"` and still match the bag's
    `THUNDERSTONE`, without either spelling becoming load-bearing.
    """
    return "".join(ch for ch in str(text).upper() if ch.isalnum())


def find_colour(d, want: str):
    """The COLOURS row whose stone matches `want`, by const OR by bag name."""
    key = _squash(want).removeprefix("ITEM")
    for row in COLOURS:
        const = row[1]
        if key in (_squash(const).removeprefix("ITEM"),
                   _squash(item_name(d, const))):
            return row
    return None


def stone_pairs(d, stone_const: str) -> list:
    """`(pre_id, pre_name, post_name)` the ROM's own table pays for a stone.

    Read out of `gEvolutionTable` rather than tabulated: `EVO_ITEM` rows carry
    the item in `param` (pokeagent/dex.py:249-251), so the cartridge names
    every pairing and a hand-written list cannot drift from it. It is also how
    GLOOM shows up under both SUN STONE and LEAF STONE without anyone
    remembering to write the second one down.
    """
    want = d.consts.items[stone_const]
    # `DexTarget.evolutions` IS the EvolutionTable (pokeagent/dex.py:1011);
    # `d.spec` is the GameSpec and has no such attribute, which is why the
    # first version of this silently answered "no pairs" for every stone.
    table = DexTarget(d.emu, d.names, d.consts, d.nav,
                      spec=d.spec).evolutions
    out = []
    for sid in range(1, 412):
        try:
            evos = table.evolutions(sid)
        except Exception:  # noqa: BLE001
            continue
        for evo in evos:
            if evo.item != want:
                continue
            pre, post = _safe_species(d, sid), _safe_species(d, evo.to_species)
            if pre and post:
                out.append((sid, pre, post))
    return out


def pick_target(d, stone_const: str):
    """Which pre-evolution to spend the stone on, and what it becomes.

    Chooses a species this save actually OWNS whose result is not yet
    registered -- the whole point of the errand is a dex entry, and a second
    LUDICOLO is a wasted stone. Party before boxes, because a party member
    needs no storage trip.
    """
    dex = DexTarget(d.emu, d.names, d.consts, d.nav, spec=d.spec)
    try:
        caught, _seen = dex.dex_flags(d.state)
    except Exception:  # noqa: BLE001
        caught = frozenset()
    party = {}
    for mon in d.state.party():
        if mon.is_egg:
            continue
        try:
            party[d.names.species(mon.species).upper()] = True
        except Exception:  # noqa: BLE001
            continue
    boxes = {}
    for slot, mon in dex.boxed():
        try:
            boxes.setdefault(d.names.species(mon.species).upper(), slot)
        except Exception:  # noqa: BLE001
            continue
    fallback = None
    for pre_id, pre, post in stone_pairs(d, stone_const):
        have = pre in party or pre in boxes
        if not have:
            continue
        post_id = next((s for s in range(1, 412)
                        if _safe_species(d, s) == post), None)
        if post_id is not None and post_id in caught:
            fallback = fallback or (pre, post)
            continue
        return (pre, post)
    return fallback


def _safe_species(d, sid: int) -> str:
    try:
        return (d.names.species(sid) or "").upper()
    except Exception:  # noqa: BLE001
        return ""


def _has(d, item: str) -> bool:
    """Bag membership WITHOUT opening the bag.

    `gBagPockets` is re-pointed while the bag UI is up, so every check has to
    happen from the field. This reads the parsed snapshot and never presses a
    key.
    """
    want = item.upper()
    try:
        for pocket in d.state.bag().values():
            if isinstance(pocket, dict) and any(
                    str(k).upper() == want for k in pocket):
                return True
    except Exception as exc:  # noqa: BLE001
        log.debug("  bag read: %s", str(exc)[:70])
    return False


def _items_pocket(d) -> dict:
    try:
        return dict(d.state.bag().get("items") or {})
    except Exception:  # noqa: BLE001
        return {}


def _enable_surf(d) -> bool:
    """`nav.surfing` is off by default and nav treats water as wall, so
    Route124 -- which is nothing but water -- is unreachable until this runs."""
    try:
        if (d.field_moves() or {}).get("SURF"):
            d.nav.surfing = True
            return True
    except Exception as exc:  # noqa: BLE001
        log.debug("  surf check: %s", str(exc)[:70])
    return False


#: A sea route is not a Fly destination, so the last hop has to be walked --
#: and walked from somewhere that touches it. Left to itself, `travel` from
#: Oldale's Pokemon Center spent four minutes and stopped at MauvilleCity with
#: "no walkable route from MauvilleCity to Route124": half a continent of
#: pathing for a map whose own connections name two Fly stops. Route124 is
#: 80x80 and connects left to LilycoveCity at offset 10 and right to
#: MossdeepCity at offset 40 (pret/data/maps/Route124/map.json), so Lilycove
#: lands at x~0, y 10-49 -- the short side for the shard at (31,53).
GATEWAYS = {
    "Route124": ("LilycoveCity", "MossdeepCity"),
    "Route126": ("SootopolisCity", "MossdeepCity", "LilycoveCity"),
}


def _travel(d, map_name: str, budget: float) -> bool:
    for _ in range(3):
        try:
            if d.travel(map_name, on_battle="fight", budget_s=budget):
                return True
        except TravelInterrupted:
            d.fight()
            d.advance_scene(40_000)
        except TravelError as exc:
            log.info("  travel %s: %s", map_name, str(exc)[:110])
            break
        if d.map_name() == map_name:
            return True
    return d.map_name() == map_name


def _reach(d, map_name: str, budget: float = 420.0) -> bool:
    """Get to a map from wherever the last job parked the save.

    Fly first: the Ever Grande plateau has no walkable route to anywhere, and
    Fly is refused indoors, so step outside before trying. Routes are not Fly
    targets, so for those the flight goes to a town the route CONNECTS TO and
    only the last leg is walked.
    """
    if d.map_name() == map_name:
        return True
    try:
        if not d.flight.flyable_here():
            d.flight.step_outside()
        if d.fly_to(map_name):
            log.info("  flew to %s", d.map_name())
            return True
    except Exception as exc:  # noqa: BLE001
        log.debug("  fly %s: %s", map_name, str(exc)[:80])
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
            log.info("  fly %s: %s", town, str(exc)[:80])
    return _travel(d, map_name, budget)


#: A blocked step turns the player without moving them, which is the only way
#: to face a cell an object is standing on.
NEIGHBOURS = (("U", 0, -1), ("D", 0, 1), ("L", -1, 0), ("R", 1, 0))


def _approach_reachable(d, x: int, y: int) -> bool:
    """Can the player get to a cell NEXT TO (x,y) on this map right now?

    The question `take_warp` asks internally. A warp tile is itself
    `collision=1` -- Route124 (70,48) reads `kind='warp'` with collision set
    -- so the test has to be about its neighbours, never the tile.
    """
    here = d.map_name()
    mine = _here_reachable(d, here)
    if not mine:
        return False
    return any((x + dx, y + dy) in mine for _, dx, dy in NEIGHBOURS)


def _leave_pocket(d, map_name: str, x: int, y: int, tries: int = 2) -> bool:
    """Fly off a sealed-off pocket of `map_name` and walk back in.

    Fly is legal from a sea route and lands in a town, and a town enters the
    route through a map CONNECTION -- which is on the open-sea side of the
    reef. Two hops out of a lagoon no surfing can leave.
    """
    for town in (GATEWAYS.get(map_name) or ()) * tries:
        try:
            if not d.flight.flyable_here():
                d.flight.step_outside()
            if not d.fly_to(town):
                continue
        except Exception as exc:  # noqa: BLE001
            log.info("  fly %s: %s", town, str(exc)[:80])
            continue
        log.info("  flew out to %s", d.map_name())
        if not _travel(d, map_name, 420.0):
            continue
        if _approach_reachable(d, x, y):
            log.info("  back on %s at %s, (%d,%d) now approachable",
                     d.map_name(), d.pos(), x, y)
            return True
    return False


def _here_reachable(d, map_name=None) -> set:
    """Every cell the player can currently walk or surf to on this map."""
    here = map_name or d.map_name()
    try:
        return set(d.nav.reachable(here, d.pos(), d.elevation()))
    except Exception as exc:  # noqa: BLE001
        log.info("  reachable(%s): %s", here, str(exc)[:80])
        return set()


def _component_of(d, map_name: str, x: int, y: int) -> set:
    """The component a BALL sits in, entered from a walkable neighbour.

    The ball's own cell is an object_event and blocks, so the component is
    asked of a neighbour -- and elevation is part of the query, because a
    sandbar is level 3 while the sea around it is level 1.
    """
    for _, dx, dy in NEIGHBOURS:
        cell = d.nav.cell(map_name, x + dx, y + dy)
        if cell is None or cell.collision:
            continue
        try:
            return set(d.nav.reachable(map_name, (x + dx, y + dy),
                                       cell.elevation))
        except Exception:  # noqa: BLE001
            continue
    return set()


def dive_plan(d, map_name: str, x: int, y: int):
    """How to reach a cell that no amount of surfing can get to.

    ROUTE 124 IS NOT AN OPEN SEA. It is a rock maze, and three of its four
    shard balls sit in lagoons that are sealed at the surface: the Blue Shard
    at (31,53) lives on a sandbar inside a 308-cell pocket whose boundary is
    solid `collision=1` reef and which touches NO map edge, so `travel` and
    `goto` are both right to answer "no-path". Two runs and ten minutes went
    into "the routing is broken" before the grid said otherwise.

    The way in is the one the map's own name advertises: Route124's
    connections include a `dive` seam to Underwater1
    (pret/data/maps/Route124/map.json), and the seafloor tunnels pass under
    the reef. `Route124_DivingTreasureHuntersHouse` is called that for a
    reason -- the shards are diving treasure.

    So: find a diveable tile in the component we are in, whose UNDERWATER
    component contains a surfacing tile inside the ball's component. Computed
    rather than tabulated, because the same answer is needed for the Red
    (28,12), Yellow (58,11) and Green balls and a hand-copied triple per
    colour is three chances to be wrong. Returns
    ``(underwater_map, dive_from, surface_at)`` or None.
    """
    mine = _here_reachable(d, map_name)
    target = _component_of(d, map_name, x, y)
    if not target:
        return None
    if target & mine:
        return None                    # already on the right side of the reef
    for edge in d.nav.exits(map_name):
        if edge.get("kind") != "dive" or edge.get("direction") != "dive":
            continue
        under = edge["dest"]
        gates = [g for g in d.nav.dive_gates(map_name, "dive") if g in mine]
        if not gates:
            continue
        surfaces = [s for s in d.nav.dive_gates(under, "emerge")
                    if s in target]
        # ONE BFS, NOT ONE PER GATE. Filling from each of the 116 diveable
        # tiles in our own component took three seconds of pure planning;
        # filling from the handful of surfacing tiles under the lagoon and
        # intersecting answers the same question.
        for spot in surfaces:
            cell = d.nav.cell(under, *spot)
            if cell is None or cell.collision:
                continue
            try:
                pool = set(d.nav.reachable(under, spot, cell.elevation))
            except Exception:  # noqa: BLE001
                continue
            entry = next((g for g in gates if g in pool), None)
            if entry:
                return (under, entry, spot)
    return None


def _ensure_diver(d, prefer: str = "") -> bool:
    """Put a DIVE knower in the party, teaching HM08 if need be.

    Nothing in this party can learn it -- PELIPPER's TMHM learnset is
    TM03/06/07/10/13/14/15/17/18/21/27/32/34/40/42/43/44/45/46/47 plus HM02
    and HM03 and stops there (pret/src/data/pokemon/tmhm_learnsets.h,
    SPECIES_PELIPPER) -- so the knower has to come out of the boxes, and two
    dozen boxed water types qualify.

    `prefer` names the stone's own target, and it is worth more than the
    level: LOMBRE learns DIVE, so withdrawing LOMBRE spends ONE party slot on
    both jobs -- it opens the lagoon and then it is standing in the party when
    the stone comes back. Otherwise the highest level goes, taking the fewest
    risks in a tunnel that is wall-to-wall encounters.
    """
    if d.can_dive():
        return True
    if not _has(d, "HM08"):
        log.info("no HM08 in the bag, so DIVE cannot be taught")
        return False
    teacher = Teacher(d)
    hm08 = teacher._item_id("HM08")
    want = (prefer or "").upper()
    dex = DexTarget(d.emu, d.names, d.consts, d.nav, spec=d.spec)
    divers = []
    for slot, boxed in dex.boxed():
        try:
            if not d.names.learns_tm(boxed.species, hm08):
                continue
            name = d.names.species(boxed.species).upper()
            divers.append((name == want, dex.boxed_level(boxed) or 0, slot,
                           name))
        except Exception:  # noqa: BLE001
            continue
    if not divers:
        log.info("no boxed Pokemon can learn DIVE")
        return False
    divers.sort(reverse=True)
    _wanted, level, slot, species = divers[0]
    log.info("DIVE: withdrawing %s L%s from box %d slot %d (prefer %s)",
             species, level, slot // BOX_SIZE, slot % BOX_SIZE,
             want or "-")
    # A PC FIRST, then the slot. `make_room` only visits a Center when the
    # party is actually full, and the withdraw needs one either way.
    if not to_center(d):
        log.info("no Pokemon Center reached (at %s)", d.map_name())
        return False
    if not make_room(d):
        return False
    st = Storage(d)
    if not st.pc_cells():
        log.info("no PC on %s", d.map_name())
        return False
    if not st.withdraw(slot // BOX_SIZE, slot % BOX_SIZE):
        log.info("withdraw refused: %s", getattr(st, "last_reason", "?"))
        return False
    st.close()
    mon = next((m for m in d.state.party()
                if not m.is_egg
                and d.names.species(m.species).upper() == species), None)
    if mon is None:
        log.info("%s is not in the party after withdraw", species)
        return False
    tutor = Teacher(d)
    ok = tutor.teach("HM08", mon.nickname or species)
    log.info("teach HM08 -> %s (%s) | DIVE = %s", ok,
             getattr(tutor, "last_reason", None),
             (d.field_moves() or {}).get("DIVE"))
    return d.can_dive()


def _swim_to(d, x: int, y: int, tries: int = 12) -> bool:
    """Cross the seafloor STEP BY STEP, fighting what swims into the way.

    `goto` across Underwater1 answered "stalled 12x at (30,45) heading for
    (10,60)" -- a counter, with no diagnosis and no progress kept. The path
    itself is fine: nav plans
    `DDDDDDDDDDDDDDDDDDDDDDLDDDDRDDDDDDDDDLLLLLLUULLULLLULLLLLLLLUL`, 62
    steps of one-tile-wide tunnel, and step-walking it gets 47 steps in before
    a CHINCHOU appears and `last_step_reason` becomes "scene-owns-input
    (gPlayerAvatar.preventStep)". Every metre of that tunnel is an encounter
    tile, so the walk has to expect a battle rather than treat one as a wall.

    `find_path` returns DIRECTION KEYS, not cells -- indexing them as
    coordinates is an IndexError, which is how the first version of this
    function died.
    """
    here = d.map_name()
    for attempt in range(tries):
        if d.pos() == (x, y):
            return True
        path = d.nav.find_path(here, d.pos(), (x, y), start_z=d.elevation())
        if not path:
            log.info("  seafloor: no path %s -> (%d,%d)", d.pos(), x, y)
            return False
        for key in path:
            before = d.pos()
            try:
                moved = d.step_dir(key) and d.pos() != before
            except TravelInterrupted:
                moved = False
            if moved:
                continue
            if d.state.in_battle() or d.scene_active():
                # A wild on the seafloor. Fight it and replan from wherever
                # the battle left us.
                try:
                    d.fight()
                except Exception as exc:  # noqa: BLE001
                    log.info("  seafloor fight: %s", str(exc)[:80])
                d.advance_scene(40_000)
                unwedge(d)
                break
            log.info("  seafloor refused %s at %s: %s | here %s | preds %s",
                     key, d.pos(), d.last_step_reason,
                     d.nav.cell(here, *d.pos()),
                     {k: d.predict_step(k) for k in ("U", "D", "L", "R")})
            return False
        if d.pos() == (x, y):
            return True
    log.info("  seafloor: %d attempts and still at %s", tries, d.pos())
    return False


def go_diving(d, plan) -> bool:
    """Walk to the dive tile, sink, cross the seafloor, surface."""
    under, (gx, gy), (sx, sy) = plan
    log.info("dive plan: %s (%d,%d) -> %s -> surface at (%d,%d)",
             d.map_name(), gx, gy, under, sx, sy)
    surface_map = d.map_name()
    if d.pos() != (gx, gy):
        try:
            if not d.goto(gx, gy, on_battle="fight"):
                log.info("could not reach the dive tile (%d,%d): %s", gx, gy,
                         d.last_goto_reason)
                return False
        except TravelInterrupted:
            d.fight()
            d.advance_scene(40_000)
            if not d.goto(gx, gy, on_battle="fight"):
                return False
    if not d.dive():
        log.info("dive refused at (%d,%d): %s", gx, gy, d.last_field_reason)
        return False
    log.info("underwater: %s %s", d.map_name(), d.pos())
    # BANK THE DESCENT. Getting here costs a Fly, a storage errand and a surf
    # across a sea route; a failure in the tunnel should not charge that again.
    if d.state_path:
        d.save(d.state_path)
    if not _swim_to(d, sx, sy):
        log.info("no seafloor path to (%d,%d) from %s", sx, sy, d.pos())
        return False
    if not d.dive():
        log.info("could not surface at (%d,%d): %s", sx, sy,
                 d.last_field_reason)
        return False
    log.info("surfaced on %s at %s", d.map_name(), d.pos())
    return d.map_name() == surface_map


def _walk_beside(d, x: int, y: int) -> bool:
    """Stand on a walkable neighbour of (x,y).

    THIS IS THE STEP `talk_to` CANNOT DO ALONE on a big map. Route124 is
    80x80 and the engine only spawns object_events near the camera, so from
    the Lilycove edge at (2,25) `talk_to(31,53)` answered "nothing at (31,53)
    on Route124 answered an A press" five times without the player taking a
    single step: the ball does not exist yet. Walking into range first makes
    it spawn, and only then is there anything to talk to.
    """
    here = d.map_name()
    for _, dx, dy in NEIGHBOURS:
        nx, ny = x + dx, y + dy
        cell = d.nav.cell(here, nx, ny)
        if cell is None or cell.collision:
            continue
        if d.pos() == (nx, ny):
            return True
        try:
            if d.goto(nx, ny, map_name=here, on_battle="fight"):
                return True
        except TravelInterrupted:
            d.fight()
            d.advance_scene(40_000)
            if d.goto(nx, ny, map_name=here, on_battle="fight"):
                return True
        except Exception as exc:  # noqa: BLE001
            log.info("  goto (%d,%d): %s", nx, ny, str(exc)[:80])
        log.info("  (%d,%d): %s", nx, ny, d.last_goto_reason)
    return False


def _pick_up(d, x: int, y: int, item: str, tries: int = 4) -> bool:
    """Face an item ball and press A until the item lands in the bag.

    An item ball is an object_event, so it BLOCKS its own cell: `goto` onto it
    can never arrive. Walk beside it, face it, press A. Route124 is open
    water, so a wild mid-approach raises TravelInterrupted -- fight it and try
    again rather than losing the errand.
    """
    for attempt in range(tries):
        if _has(d, item):
            return True
        if not _walk_beside(d, x, y):
            log.info("  %s try %d: no walkable neighbour of (%d,%d) reached "
                     "(at %s)", item.lower(), attempt + 1, x, y, d.pos())
            continue
        px, py = d.pos()
        want = next((k for k, dx, dy in NEIGHBOURS
                     if (px + dx, py + dy) == (x, y)), None)
        log.info("  %s try %d: beside it at (%d,%d), facing %s", item.lower(),
                 attempt + 1, px, py, want)
        for _ in range(8):
            if _has(d, item):
                return True
            if want:
                d.step_dir(want)
            d.emu.run_sequence("A:4 .:40")
            d.advance_scene(40_000)
        try:
            d.talk_to(x, y)
        except TravelInterrupted:
            log.info("  %s: wild on the way -- fighting", item.lower())
            d.fight()
            d.advance_scene(40_000)
            continue
        except Exception as exc:  # noqa: BLE001
            log.info("  %s: talk_to raised: %s", item.lower(), str(exc)[:90])
        for _ in range(6):
            if _has(d, item):
                return True
            d.emu.run_sequence("A:4 .:40")
            d.advance_scene(40_000)
    return _has(d, item)


def rom_labels(d, list_id: int) -> list:
    """Decode ONE `gMultichoiceLists` entry, by index.

    `Menus.multichoice_labels` filters by option count and so returns every
    list of that length; naming the id the script will actually use turns the
    expected option order into a cartridge-verified fact.
    `struct MultichoiceListStruct { const struct MenuAction *list; u8 count; }`
    and `struct MenuAction { const u8 *text; void (*func)(void); }` are both
    8 bytes (pret/src/script_menu.c:482).
    """
    try:
        base = d.emu.resolve("gMultichoiceLists")
        ptr, n = struct.unpack("<II", bytes(d.emu.read(base + list_id * 8, 8)))
        raw = bytes(d.emu.read(ptr, n * 8))
        out = []
        for j in range(n):
            tp = struct.unpack_from("<I", raw, j * 8)[0]
            out.append(d.emu.charmap.decode(
                bytes(d.emu.read(tp, 16))).strip() if tp else "")
        return out
    except Exception as exc:  # noqa: BLE001
        log.debug("  list %d: %s", list_id, str(exc)[:80])
        return []


def shards_held(d) -> list:
    """The colours in the bag, in the multichoice's own order.

    `COLOURS` stores ROM constants, so membership has to go through
    `item_name` -- testing the bag for the string "ITEM_YELLOW_SHARD" is a
    quiet False, which would collapse every index to 0 and pick the wrong
    trade the first time two colours were carried at once.
    """
    return [item_name(d, row[0]) for row in COLOURS
            if _has(d, item_name(d, row[0]))]


def trade_index(d, rank: int) -> int:
    """Where the colour of rank `rank` sits in the box that is about to open.

    The order is RED, YELLOW, BLUE, GREEN filtered to what is in the bag
    (scripts.inc:88-223), so the index is the number of lower-ranked colours
    held. MUST be computed from the field, before any menu is open.
    """
    return sum(1 for i, row in enumerate(COLOURS)
               if i < rank and _has(d, item_name(d, row[0])))


#: The two script boxes this conversation puts up, named from the symbol
#: table (pret/src/script_menu.c:670, :766) so the predicates are exact.
MULTICHOICE_TASK = "Task_HandleMultichoiceInput"
YESNO_TASK = "Task_HandleYesNoInput"


def _wait_task(d, task: str, presses: int = 40) -> bool:
    """Advance a conversation until `task` owns input.

    `gMenu`'s cursor and bounds are LEFTOVERS until a box is drawn -- they
    read as a plausible open menu while a message is still printing, and every
    d-pad press vanishes into the dialog. The task name comes from the symbol
    table, so this is exact. Pressing A is safe only because the box we want
    is not up yet: there is no option to select by accident.

    THE WAIT MATTERS AS MUCH AS THE PRESS. `Menus.wait_for_choice` allows the
    box 28 frames per press, and this man's three message boxes need ten
    presses each at that rate -- so sixteen presses ran out mid-greeting and
    the run reported "no option box" three times with the box one A away.
    `settle` lets the text finish printing, and then five presses are enough.
    """
    for _ in range(presses):
        if task in d.state.tasks():
            return True
        d.settle(400)
        if task in d.state.tasks():
            return True
        d.emu.run_sequence("A:4 .:60")
    return task in d.state.tasks()


def to_overworld(d) -> bool:
    """Give input back to the player if the save is sitting on the title.

    Winning the Champion fight ends in `special GameClear`
    (pret/data/maps/EverGrandeCity_HallOfFame/scripts.inc,
    EventScript_GameClearMale): it saves, rolls the credits and drops the game
    back to the intro. A savestate taken anywhere in that stretch reads as a
    perfectly ordinary field state -- SaveBlock RAM is intact, so `map_name()`
    answers EverGrandeCity_HallOfFame (7,5) and the party and dex parse -- yet
    the player cannot take a single step, because `gMain.callback2` is
    `MainCB2_Intro`, not `CB2_Overworld`. `scene_active()` reads False, so
    `unwedge` returns True immediately and every caller downstream burns its
    budget on "stalled 12x at (7,5)". `Driver.at_title` keys off the callback
    instead, which is the only reliable signal.
    """
    if not d.at_title():
        return True
    log.info("save is on the title (callback %s) -- resuming",
             d.state.callback_name())
    ok = d.resume_from_title()
    log.info("resume_from_title -> %s | %s %s", ok, d.map_name(), d.pos())
    return ok


def trade(d, want: str) -> bool:
    """Fetch the matching shard and swap it for `want`."""
    row = find_colour(d, want)
    if row is None:
        log.info("no shard trades for %r; the hunter offers %s", want,
                 [item_name(d, r[1]) for r in COLOURS])
        return False
    shard_const, stone_const, bit, ball_map, bx, by, list_id = row
    shard = item_name(d, shard_const)
    stone = item_name(d, stone_const)
    rank = COLOURS.index(row)
    log.info("trading %s -> %s (rank %d, mask bit %d)", shard, stone, rank,
             bit)

    if _has(d, stone):
        log.info("%s already in the bag", stone)
        return True

    to_overworld(d)
    unwedge(d)
    _enable_surf(d)
    try:
        if not d.flight.flyable_here():
            # `heal_at_nearest_center` cannot leave the Elite Four plateau --
            # the interior is a one-way chain and Fly is refused indoors -- so
            # a save parked at HallOfFame has no route anywhere. `to_center`
            # walks out through the map's own warps until Fly is accepted.
            to_center(d)
            log.info("escaped to %s", d.map_name())
    except Exception as exc:  # noqa: BLE001
        log.info("escape: %s", str(exc)[:90])

    if not _has(d, shard):
        # THE DIVER COMES FIRST, while a Pokemon Center's PC is still one Fly
        # away. Teaching HM08 needs a box and a free party slot, and neither
        # exists in the middle of Route 124.
        if not _reach(d, ball_map):
            log.info("could not reach %s (at %s)", ball_map, d.map_name())
            return False
        plan = dive_plan(d, ball_map, bx, by)
        if plan is not None:
            log.info("%s at (%d,%d) is sealed off by reef -- diving", shard,
                     bx, by)
            target = pick_target(d, stone_const)
            if not _ensure_diver(d, prefer=target[0] if target else ""):
                log.info("no DIVE, so the %s lagoon cannot be entered", shard)
                return False
            # BANK THE DIVER. The withdraw and the HM are a Fly plus a storage
            # errand; losing them to a failure out at sea charges it twice.
            if d.state_path:
                d.save(d.state_path)
            _enable_surf(d)
            if not _reach(d, ball_map):
                log.info("could not get back to %s (at %s)", ball_map,
                         d.map_name())
                return False
            plan = dive_plan(d, ball_map, bx, by)
            if plan is None:
                log.info("no dive route into the %s lagoon", shard)
                return False
            if not go_diving(d, plan):
                return False
        # CHECKPOINT ON ARRIVAL. Getting here is a Fly, a surf across a sea
        # route and a seafloor crossing, fighting whatever swims into it.
        # Banking makes a retry of the pickup cost seconds, and the script is
        # idempotent: every step is guarded on bag membership.
        if d.state_path:
            d.save(d.state_path)
        _pick_up(d, bx, by, shard)
    if not _has(d, shard):
        log.info("no %s: the ball at %s (%d,%d) gave nothing", shard,
                 ball_map, bx, by)
        return False
    log.info("%s held (mask bit %d)", shard, bit)

    # THE INDEX IS DECIDED HERE, in the field, while the bag is readable.
    index = trade_index(d, rank)
    log.info("expect option %d of %s (ROM list %d = %s)", index,
             shards_held(d), list_id, rom_labels(d, list_id))

    if d.map_name() != HOUSE:
        warp_map, wx, wy = HOUSE_WARP
        if not _reach(d, warp_map):
            log.info("could not reach %s (at %s)", warp_map, d.map_name())
            return False
        # LEAVE THE LAGOON. Being on the right MAP is not being on the right
        # side of the reef: with the shard in hand the player is still sealed
        # inside the pocket, and `take_warp` would answer "no approach to
        # warp (70,48)" while standing forty tiles away with no route. The
        # dive route is one-way in practice -- the seafloor tunnel surfaces
        # where it surfaces -- so the cheap exit is Fly, which is legal from a
        # sea route, and then walk in through a map connection.
        if not _approach_reachable(d, wx, wy):
            log.info("sealed off from the warp at (%d,%d) -- flying out",
                     wx, wy)
            if not _leave_pocket(d, warp_map, wx, wy):
                log.info("could not get to the (%d,%d) side of %s", wx, wy,
                         warp_map)
                return False
        # Scripts open doors with `setmetatile`, so the static grid can still
        # read a wall where one stands open.
        try:
            d.sync_grid()
        except Exception:  # noqa: BLE001
            pass
        try:
            d.take_warp(wx, wy)
        except TravelInterrupted:
            d.fight()
            d.advance_scene(40_000)
            d.take_warp(wx, wy)
        if d.map_name() != HOUSE:
            log.info("warp (%d,%d) did not enter the house: %s / %s",
                     wx, wy, d.map_name(), d.last_warp_reason)
            return False
    log.info("inside %s at %s", d.map_name(), d.pos())

    menus = Menus(d.emu, d.state)
    hx, hy = HUNTER
    for attempt in range(3):
        if _has(d, stone):
            break
        try:
            d.talk_to(hx, hy)
        except Exception as exc:  # noqa: BLE001
            log.info("talk_to(%d,%d): %s", hx, hy, str(exc)[:90])
        if not _wait_task(d, MULTICHOICE_TASK):
            log.info("attempt %d: no option box (%s)", attempt + 1,
                     (d.state.message() or "")[:70])
            unwedge(d)
            continue
        lo, hi = menus.bounds()
        log.info("attempt %d: box open, cursor bounds %d..%d -> picking %d",
                 attempt + 1, lo, hi, index)
        if not menus.select_index(index):
            log.info("select_index(%d): %s", index, menus.last_reason)
            unwedge(d)
            continue
        # `msgbox ... MSGBOX_YESNO` (scripts.inc:248) prints, then
        # `ScriptMenu_YesNo` creates Task_HandleYesNoInput
        # (pret/src/script_menu.c:754-766). Never blind-press: the default
        # lands on NO.
        if not _wait_task(d, YESNO_TASK):
            log.info("no YES/NO box after option %d (%s)", index,
                     (d.state.message() or "")[:70])
            unwedge(d)
            continue
        if not menus.resolve_choice("YES"):
            log.info("resolve_choice: %s", menus.last_reason)
            unwedge(d)
            continue
        # `giveitem` prints, then the script re-checks the mask; with the last
        # shard spent VAR_TEMP_1 is 0 and it releases (scripts.inc:264-266).
        d.advance_scene(60_000)
        unwedge(d)

    got = _has(d, stone)
    log.info("ITEMS pocket now: %s", _items_pocket(d))
    log.info("%s held = %s | %s held = %s", stone, got, shard, _has(d, shard))
    # BANK THE STONE THE MOMENT IT LANDS. The YELLOW leg completed the trade
    # and then reported failure on a name mismatch, and because the save only
    # happened on the success path the THUNDERSTONE went with it -- ten
    # minutes of surfing, diving and a one-shot item ball flag, thrown away by
    # a bookkeeping error. The trade is the expensive, irreversible half.
    if got and d.state_path:
        d.save(d.state_path)
    return got


def make_room(d) -> bool:
    """Free a party slot WITHOUT stripping a held item into a box.

    A stone is used on a party member, so a boxed target has to be withdrawn
    and a full party has to give up a slot. `stone_evolve` deposits the
    lowest-level mon, which here is the L27 CORPHISH carrying the EXP. SHARE --
    and a mon deposited while holding an item takes the item into the box with
    it (share_grind.py:12-18), which would quietly end another agent's grind.
    So pick the lowest-level mon that is carrying NOTHING.
    """
    party = d.state.party()
    if len([m for m in party if not m.is_egg]) < 6:
        return True
    cands = [(i, m) for i, m in enumerate(party)
             if not m.is_egg and not m.held_item]
    if not cands:
        log.info("every party member holds an item; not stripping one")
        return True
    i, victim = min(cands, key=lambda p: p[1].level or 0)
    if not to_center(d):
        log.info("no Pokemon Center reached (at %s)", d.map_name())
        return False
    st = Storage(d)
    if not st.pc_cells():
        log.info("no PC on %s", d.map_name())
        return False
    log.info("depositing %s L%s (holds nothing) to free a slot",
             victim.nickname, victim.level)
    ok = st.deposit(i)
    st.close()
    if not ok:
        log.info("deposit refused: %s", getattr(st, "last_reason", "?"))
    return ok


def dex_count(d) -> int:
    import re

    dex = DexTarget(d.emu, d.names, d.consts, d.nav, spec=d.spec)
    m = re.search(r"dex (\d+)/", dex.summary(d.state))
    return int(m.group(1)) if m else -1


def spend(d, stone: str, species: str, becomes: str) -> int:
    """Use the stone on `species`, withdrawing it from a box if need be.

    Done here rather than by handing off to `scripts/stone_evolve.py`, and
    the Yellow Shard leg is why: that script's pair table spells the item
    "THUNDER STONE" while the bag holds `THUNDERSTONE`, so its own
    `held()` check refuses a stone that is sitting in the ITEMS pocket. This
    resolves every name off the cartridge, so there is nothing to spell wrong.
    """
    if not _has(d, stone):
        log.info("no %s in the bag -- nothing to spend", stone)
        return 1
    before = dex_count(d)
    mon = next((m for m in d.state.party()
                if not m.is_egg
                and d.names.species(m.species).upper() == species), None)
    if mon is None:
        dex = DexTarget(d.emu, d.names, d.consts, d.nav, spec=d.spec)
        slot = next((s for s, b in dex.boxed()
                     if _safe_species(d, b.species) == species), None)
        if slot is None:
            log.info("no %s in the party or the boxes", species)
            return 1
        log.info("%s is boxed at box %d slot %d", species, slot // BOX_SIZE,
                 slot % BOX_SIZE)
        if not to_center(d):
            log.info("no Pokemon Center reached (at %s)", d.map_name())
            return 1
        if not make_room(d):
            return 1
        st = Storage(d)
        if not st.pc_cells():
            log.info("no PC on %s", d.map_name())
            return 1
        if not st.withdraw(slot // BOX_SIZE, slot % BOX_SIZE):
            log.info("withdraw refused: %s", getattr(st, "last_reason", "?"))
            return 1
        st.close()
        mon = next((m for m in d.state.party()
                    if not m.is_egg
                    and d.names.species(m.species).upper() == species), None)
        if mon is None:
            log.info("%s is not in the party after withdraw", species)
            return 1
    nick = mon.nickname or species
    log.info("using %s on %s L%s", stone, nick, mon.level)
    tutor = Teacher(d)
    ok = tutor.use_on_mon(stone, nick)
    log.info("use_on_mon -> %s (%s)", ok, getattr(tutor, "last_reason", None))
    d.advance_scene(60_000)
    unwedge(d)
    now = dex_count(d)
    log.info("party now: %s",
             [(d.names.species(m.species), m.level)
              for m in d.state.party() if not m.is_egg])
    log.info("dex %d -> %d", before, now)
    if d.state_path:
        d.save(d.state_path)
    if now > before:
        log.info("*** %s REGISTERED ***", becomes.upper())
        return 0
    log.info("%s did not register", becomes.upper())
    return 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True)
    ap.add_argument("--want", default="WATER STONE",
                    help="the stone to trade for, by bag name or ITEM_ const")
    ap.add_argument("--species", default=None,
                    help="pre-evolution to spend it on; default from the ROM")
    ap.add_argument("--no-evolve", action="store_true",
                    help="bank the stone and stop")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    d = Driver(a.state)
    to_overworld(d)
    d.advance_scene(40_000)
    log.info("start %s %s | dex %d | ITEMS %s", d.map_name(), d.pos(),
             dex_count(d), _items_pocket(d))

    row = find_colour(d, a.want)
    if row is None:
        log.info("no shard trades for %r; the hunter offers %s", a.want,
                 [item_name(d, r[1]) for r in COLOURS])
        return 1
    stone_const = row[1]
    stone = item_name(d, stone_const)

    if not trade(d, a.want):
        log.info("TRADE FAILED for %s", stone)
        return 1
    log.info("*** %s OBTAINED ***", stone)
    if a.no_evolve:
        return 0

    target = pick_target(d, stone_const)
    if a.species:
        pairs = {pre: post for _i, pre, post in stone_pairs(d, stone_const)}
        species = a.species.upper()
        target = (species, pairs.get(species, "?"))
    if target is None:
        log.info("nothing this save owns evolves with a %s (ROM pairs: %s)",
                 stone, [(p, q) for _i, p, q in stone_pairs(d, stone_const)])
        return 0
    species, becomes = target
    log.info("=== spending %s on %s -> %s ===", stone, species, becomes)
    return spend(d, stone, species, becomes)


if __name__ == "__main__":
    raise SystemExit(main())
