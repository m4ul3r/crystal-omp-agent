#!/usr/bin/env python
"""Register CLAYDOL and BANETTE from Sky Pillar 1F.

Sky Pillar 1F is **L47-50 CLAYDOL 25% / BANETTE 15% / GOLBAT 30% /
SABLEYE 30%** on land (`pret/src/data/wild_encounters.json:14070-14137`,
`docs/gen3/guide/encounters.json:12168+`). Claydol and Banette are the only
two dex slots the floor still owes, they need no item and no field move, and
nothing gates the floor -- only the crumbling floors on 2F and 4F want the
Mach Bike, and this never goes above 1F.

**Never go above 1F.** `pokeagent/behaviors.py` does not model
`MB_CRACKED_FLOOR`, so nav plans straight across 2F's and 4F's crumbling
tiles and the player falls through. 1F has no cracked floor at all; the only
way off it is the stairs warp at (10,1), so anything that lands us upstairs
comes straight back down (`back_to_floor`).

Route, every cell from the maps' own `warp_events`:
`Route131 (36,6)` -> `SkyPillar_Entrance` arrive (6,16) -> walk (14,4) ->
`SkyPillar_Outside` arrive (17,13) -> walk (14,5) -> `SkyPillar_1F` arrive
(6,13).

**The route only exists after the Champion.** Route131 swaps to map layout
320 on `FLAG_SYS_GAME_CLEAR` (`pret/data/maps/Route131/scripts.inc:6-11`),
and that layout is the one with the channel to the door in it. `nav` decodes
the SHIPPED layout, on which the door sits in a sealed lagoon -- which is why
six earlier sessions measured the pillar as unreachable and were right about
the map they were measuring. `open_the_pillar` syncs the live map in; see the
comment on `SEA_CHAIN`.

Healing is a Fly round trip, because there is no Center anywhere near the
pillar.
"""
import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from pokeagent.trek import Driver, TravelInterrupted  # noqa: E402
from collect import Collector  # noqa: E402
import share_grind  # noqa: E402

log = logging.getLogger("skypillar")

FLOOR = "SkyPillar_1F"
#: THE PILLAR'S DOOR IS OPEN AFTER THE CHAMPION AND NOT BEFORE, and this is
#: the fact six earlier sessions spent themselves on. `Route131_MapScripts`
#: runs `MAP_SCRIPT_ON_TRANSITION` -> `call_if_set FLAG_SYS_GAME_CLEAR` ->
#: **`setmaplayoutindex 320`** (`pret/data/maps/Route131/scripts.inc:1-11`).
#: Layout 320 is `LAYOUT_UNKNOWN_MAP_08302970`
#: (`pret/data/layouts/layouts.json`, entry 320) -- 60x40 on the same
#: General+Pacifidlog tilesets as `LAYOUT_ROUTE131`, but a DIFFERENT
#: blockdata file. It draws Sky Pillar's island and opens the channel to it.
#:
#: The harness decodes Route131 from `map.json`, which names the SHIPPED
#: layout, so `nav` sees the pre-Champion map: 233 of the 2400 cells differ in
#: (collision, elevation) between `data/layouts/Route131/map.bin` and
#: `data/layouts/UnknownMap_08302970/map.bin`, and on the shipped one the
#: strip holding the door is a sealed 523-cell lagoon. That is what session
#: port-38 measured and correctly reported as unreachable -- and it wrote off
#: the route on the strength of a map the post-game engine no longer uses.
#:
#: So there is no "northern sea" to sail to. Take the ordinary route west,
#: land in Route131's main body, and `sync_grid` the live `gBackupMapLayout`
#: into nav (`trek.py:2376`). `nav.step` reads through the live overrides
#: (`nav.py:536-537`), so the channel from the main body up column ~36 to
#: the door appears the moment the sync lands.
SEA_CHAIN = ["Route127", "Route128", "Route129", "Route130", "Route131"]

#: `Route131`'s Sky Pillar warp, and the one open cell it is entered from.
#: In layout 320 the door sits in a one-tile gap in the island wall: (36,5)
#: is rock and (36,7) is open water, so the approach is always from BELOW.
PILLAR_DOOR = (36, 6)

HOPS = [
    ("Route131", PILLAR_DOOR),
    ("SkyPillar_Entrance", (14, 4)),
    ("SkyPillar_Outside", (14, 5)),
]
#: PACIFIDLOG IS NOT FLYABLE -- this run never visited it, so its
#: FLAG_VISITED_PACIFIDLOG_TOWN is clear and the Fly map greys it out.
#: Mossdeep is the nearest landing that IS unlocked, and it has a Center.
HEAL_TOWN = "MossdeepCity"

#: Where to bank progress after every leg. Without this the voyage was thrown
#: away on any failure and every pass restarted from inside Sootopolis Gym --
#: five minutes of sailing discarded each time.
BANK: str | None = None


def bank(d) -> None:
    """Persist the voyage so the next pass resumes where this one stopped."""
    if BANK:
        try:
            d.save(BANK)
        except Exception:  # noqa: BLE001
            pass


#: Title-screen recovery lives in the LIBRARY -- `Driver.at_title()` and
#: `Driver.resume_from_title()` (`pokeagent/trek.py:457-515`). This script
#: carried its own copy for one afternoon, which is how the condition was
#: found: a state banked any time after the Champion fight is parked on the
#: title screen, because the Hall of Fame rolls the credits and soft-resets
#: the cartridge. SaveBlock RAM survives, so `map_name()`, the party and the
#: dex all read plausibly while the player cannot move at all -- and
#: `scene_active()` reads FALSE, so it cannot be used to detect it. There is
#: one copy now; do not add a second here.


def to_open_air(d) -> bool:
    """Stand somewhere Fly accepts. Fly is refused indoors (MAP_TYPE_INDOOR),
    and `Driver.resume_from_title` always lands us in a bedroom."""
    for _ in range(6):
        if d.flight.flyable_here():
            return True
        before = d.map_name()
        try:
            d.flight.step_outside()
        except Exception as exc:  # noqa: BLE001
            log.info("  step_outside: %s", str(exc)[:70])
            return False
        if d.map_name() == before:
            return False
    return d.flight.flyable_here()


def back_to_floor(d) -> bool:
    """Come back down if a walk crossed the stairs warp at 1F (10,1).

    A warp fires on the step that ENTERS it (harness gotcha 9), so a pacing
    leg that merely routes ACROSS (10,1) puts us on 2F -- and 2F is the floor
    whose `MB_CRACKED_FLOOR` tiles nav cannot see. Take the first warp that
    leads back to 1F rather than pacing a floor we do not want and cannot
    model.
    """
    if d.map_name() == FLOOR:
        return True
    if not d.map_name().startswith("SkyPillar"):
        return False
    for _ in range(4):
        here = d.map_name()
        if here == FLOOR:
            return True
        door = next((w for w in d.nav.exits(here)
                     if w.get("kind") == "warp" and w.get("dest") == FLOOR),
                    None)
        if door is None:
            # Above 2F there is no direct way down but the cracked floors
            # themselves. Walking off the edge of a crumbling tile drops one
            # floor, which is the only descent the engine offers, so let the
            # fall happen rather than pretending we can route.
            log.info("  no warp back to %s from %s", FLOOR, here)
            return False
        try:
            d.reach_cell(door["x"], door["y"] + 1, map_name=here,
                         on_battle="fight")
        except Exception:  # noqa: BLE001
            if d.in_battle():
                d.fight(policy=Driver.damage_first)
        d.take_warp(door["x"], door["y"])
        log.info("  came back down: %s -> %s %s", here, d.map_name(), d.pos())
    return d.map_name() == FLOOR


def go(d, dest, tries=5, budget_s=180.0) -> bool:
    """One sea leg. BOUNDED, because an open-water seam can pin.

    `travel` with no budget checks no deadline at all, and a refused step at a
    seam is retried for free: a pass sat on `Route130 (77,18)` for eleven
    minutes with the emulator ticking and the player never moving, which from
    outside is indistinguishable from a hang. A bounded attempt lets the retry
    re-plan from wherever it actually is.
    """
    if d.map_name() == dest:
        return True
    for _ in range(tries):
        try:
            if d.travel(dest, on_battle="fight", budget_s=budget_s):
                return True
        except TravelInterrupted:
            if d.in_battle():
                d.fight(policy=Driver.damage_first)
            d.advance_scene(40000)
        except Exception as exc:  # noqa: BLE001
            log.info("  travel %s: %s", dest, str(exc)[:70])
            if d.in_battle():
                d.fight(policy=Driver.damage_first)
            d.advance_scene(40000)
        if d.map_name() == dest:
            return True
    return d.map_name() == dest


def escape_gym(d) -> bool:
    """Out of Sootopolis Gym. Routing cannot cross its ice, so a save left on
    that floor made every `travel` call fail from the first move -- the grind
    reported "could not climb (at SootopolisCity_Gym_1F)" forever."""
    if not d.map_name().startswith("SootopolisCity_Gym"):
        return True
    if d.map_name() == "SootopolisCity_Gym_B1F":
        d.reach_cell(11, 22, map_name=d.map_name(), on_battle="fight")
        d.take_warp(11, 22)
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from ice_run import read_floor, floor_path, run_path

    for _ in range(4):
        if not d.map_name().startswith("SootopolisCity_Gym"):
            return True
        d.close_menus()
        walls, thin, cracked, _stairs = read_floor(d)
        blocked = {(o["x"], o["y"]) for o in d.live_npcs()
                   if not o.get("player")}
        path = floor_path(walls, thin, cracked, blocked, d.pos(),
                          [(8, 25), (9, 25)])
        if path is None:
            break
        run_path(d, path, d.map_name())
        for door in ((8, 25), (9, 25)):
            if d.pos() == door and d.take_warp(*door):
                break
    return not d.map_name().startswith("SootopolisCity_Gym")


def open_the_pillar(d) -> int:
    """Push Route131's POST-CHAMPION layout into nav. Returns cells synced.

    This one call is the difference between the voyage working and six
    sessions of correct pathfinding over the wrong map. `nav` decoded
    Route131 from `map.json`'s shipped layout; the engine, with
    `FLAG_SYS_GAME_CLEAR` set, is walking layout 320. `live_grid` reads
    `gBackupMapLayout` -- the map the engine is actually on -- and
    `sync_grid` installs every cell whose (collision, elevation) differs, so
    `nav.step` starts answering about the real Route131.

    Expect ~233 cells. The other ~103 that differ do so only in their
    metatile id, with identical collision and elevation, so `sync_grid`
    ignores them by design ("an override that merely restates the shipped
    data is a lie waiting to be read") and nothing about pathing changes.
    """
    if d.map_name() != "Route131":
        return 0
    try:
        synced = d.sync_grid()
    except Exception as exc:  # noqa: BLE001
        log.info("  sync_grid raised %s: %s", type(exc).__name__,
                 str(exc)[:70])
        return 0
    reach = set(d.nav.reachable("Route131", d.pos(), d.elevation()))
    below = (PILLAR_DOOR[0], PILLAR_DOOR[1] + 1)
    log.info("  synced %d live cells on Route131; %s reachable from %s: %s",
             synced, below, d.pos(), below in reach)
    return synced


def climb(d) -> bool:
    """Wherever we are -> the first floor of the pillar."""
    if not escape_gym(d):
        log.info("  stuck inside the gym at %s", d.pos())
        return False
    bank(d)
    # FLY OUT FIRST, from anywhere that is not already on the voyage.
    # Sootopolis is a crater whose only walking exit is a dive, so
    # `travel("Route131")` from inside it fails on its first move -- the grind
    # logged "could not climb (at SootopolisCity)" on a loop. The same is true
    # of everywhere on the mainland: asked to plan Littleroot -> Route127 in
    # one call, `travel` has the whole of Hoenn plus two oceans to search.
    # Mossdeep is the last landing before the sea chain and it has a Center.
    #
    # NOT from a map that IS on the voyage. This used to be unconditional, so
    # each pass flew back to Mossdeep and threw away whatever sea legs the
    # previous pass had walked -- the log was nothing but "flew to
    # MossdeepCity".
    on_voyage = (d.map_name() in SEA_CHAIN
                 or d.map_name().startswith("SkyPillar"))
    if not on_voyage and d.map_name() != HEAL_TOWN:
        if not to_open_air(d):
            log.info("  cannot Fly from %s", d.map_name())
        elif not d.fly_to(HEAL_TOWN):
            log.info("  could not fly to %s from %s", HEAL_TOWN, d.map_name())
        else:
            log.info("  flew to %s", d.map_name())
    for _ in range(12):
        if d.map_name() == FLOOR:
            return True
        here = d.map_name()
        door = dict(HOPS).get(here)
        if door is None:
            # ONE MAP CONNECTION AT A TIME. `travel` will not plan
            # Mossdeep -> Route131 in a single call -- too much open water --
            # but each of these is a single seam and goes through fine.
            if here in SEA_CHAIN:
                nxt = SEA_CHAIN[SEA_CHAIN.index(here) + 1:]
                if not nxt:
                    return False        # Route131 is in HOPS, so unreachable
                if not go(d, nxt[0], tries=3):
                    log.info("  stuck at %s heading for %s", here, nxt[0])
                    bank(d)
                    return False
                bank(d)
                continue
            for leg in SEA_CHAIN:
                if go(d, leg, tries=2):
                    break
            else:
                bank(d)
                return False
            bank(d)
            continue
        if here == "Route131":
            open_the_pillar(d)
        # APPROACH FROM BELOW, NEVER STRAIGHT AT IT. Route131's door sits in
        # a one-tile gap in the island wall, and routing at the tile itself
        # walked off the map edge into Route130/129 instead -- the log read
        # "Route131 -> Route129". Standing one tile south first keeps the
        # approach inside the map, and the warp fires on the step that ENTERS
        # the tile (harness gotcha 9).
        below = (door[0], door[1] + 1)
        for stand in (below, door):
            try:
                d.reach_cell(*stand, map_name=here, on_battle="fight")
            except Exception:  # noqa: BLE001
                if d.in_battle():
                    d.fight(policy=Driver.damage_first)
            if d.map_name() != here:
                break
            if d.pos() == stand:
                break
        if d.map_name() == here and not d.take_warp(*door):
            log.info("  %s door %s refused: %s (at %s)", here, door,
                     d.last_warp_reason, d.pos())
            return False
        bank(d)
        log.info("  %s -> %s %s", here, d.map_name(), d.pos())
    return d.map_name() == FLOOR


def heal_trip(d) -> bool:
    """Fly out, heal, come back. There is no Center near the pillar."""
    log.info("healing (lead %s)", d.state.party()[0].hp)
    # OUT OF THE CAVE FIRST. Fly is refused on an indoor map, so calling this
    # from SkyPillar_1F returned False instantly and the caller looped on it:
    # "healing (lead 85) / heal trip failed; carrying on" printed thousands of
    # times in fourteen minutes while the hunt stood still.
    if not to_open_air(d):
        log.info("  cannot Fly from %s", d.map_name())
        return False
    if not d.fly_to(HEAL_TOWN):
        return False
    # STRAIGHT TO THE CENTER DOOR. Walking whatever warps `exits()` happens to
    # list wandered into the wrong buildings and left the party hurt on the
    # far side of the ocean, which is what kept the voyage bouncing back down
    # the sea chain. The map's own warp table names the door.
    if "PokemonCenter" not in d.map_name():
        for w in (d.nav.info(HEAL_TOWN).warps or []):
            if "POKEMON_CENTER" not in str(w.dest_map):
                continue
            try:
                d.reach_cell(w.x, w.y + 1, map_name=HEAL_TOWN,
                             on_battle="fight")
            except Exception:  # noqa: BLE001
                pass
            if d.take_warp(w.x, w.y):
                break
    ok = d.heal()
    for e in d.exits():
        if e.get("kind") == "warp":
            d.take_warp(e["x"], e["y"])
            break
    log.info("healed=%s", ok)
    return ok


#: The two dex slots this floor owes. Named, because the hunt has to know when
#: it is finished: the floor is also L47-50 XP, so "keep going until the
#: budget runs out" is never wrong enough to notice, and the run has spent
#: whole sessions grinding a floor whose objective was already closed.
TARGETS = ("CLAYDOL", "BANETTE")

#: Catch multipliers, from the ROM's own table -- ULTRA 2.0, GREAT 1.5,
#: POKe 1.0 (`pret/src/battle_script_commands.c:1033-1036`, applied at
#: :9452). Ordered best first, because the binding constraint on this hunt is
#: not money, it is THROWS PER BATTLE: the ball goes in at FULL HP (an L100
#: lead makes `Catcher._would_ko` true on turn one, so there is no weakening
#: phase), the odds term is then only catch_rate * bonus / 30, and every miss
#: is another enemy turn against the party.
#:
#: At full HP that is ~24% per ULTRA BALL on CLAYDOL (catch rate 90) and ~12%
#: on BANETTE (45), against ~12% and ~6% for a POKe BALL. Nothing in a cave
#: flees, so a battle runs until the ball sticks: ~4 throws per Claydol and
#: ~9 per Banette with ULTRA, or ~9 and ~17 with POKe -- and it is the second
#: number, not the money, that decides whether the lead is still standing.
#:
#: Named by CONSTANT, not by display string: `names.item(4)` decodes to
#: `'POKé BALL'` with a charmap glyph, and `'POKE BALL'.upper()` never equals
#: it -- `'é'.upper()` is `'É'`, so even a case-folded compare misses. The
#: shelf rows carry their own item id, so match on that and hand `Mart.buy`
#: the row's own name back.
BALL_PREFERENCE = ("ITEM_ULTRA_BALL", "ITEM_GREAT_BALL", "ITEM_POKE_BALL")

#: Mossdeep's shelf is ULTRA (1200) / NET (1000) / DIVE (1000)
#: (`pret/data/maps/MossdeepCity_Mart/scripts.inc:15-25`), and Mossdeep is
#: where `climb` flies to anyway -- so this costs no detour at all, which the
#: mainland's cheaper POKe BALL shelves do.
SHOP_MART = "MossdeepCity_Mart"

#: Shop when the pocket falls below this; buy up to this many in one trip.
#: `Mart.buy` raises the quantity box ONE PRESS AT A TIME and gives up after
#: `MAX_PRESSES` (`pokeagent/mart.py:38`, 40), so a single call cannot ask for
#: more than that -- hence the chunked loop in `stock_balls` rather than one
#: big request.
BALL_FLOOR = 25
BALL_STOCK = 30


class Hunt(Collector):
    """`Collector`, restricted to one floor and one pair of species.

    Subclassed rather than copied because everything that makes catching work
    is already in there and was hard-won: the plan is computed ONCE from a
    settled frame (`battle_ready()` first, or the enemy species reads None and
    every catch is declined as a "trainer battle"), the dex check runs ahead of
    the ball reserve, and `pace_map` walks with `goto` instead of hand-stepping
    -- which spun 7.5 million refused steps in 150 seconds the one time it was
    hand-rolled.
    """

    def missing_targets(self) -> set:
        """Which of TARGETS is still not registered as CAUGHT."""
        caught, _seen = self.target.dex_flags(self.d.state)
        out = set()
        for entry in self.target.achievable:
            name = (entry.rom_name or entry.name).upper()
            if name in TARGETS and entry.natdex not in caught:
                out.add(name)
        return out


def stock_balls(hunt, mart=SHOP_MART, want=BALL_STOCK, floor=BALL_FLOOR) -> int:
    """Buy the BEST ball on `mart`'s shelf, before the voyage rather than after.

    Not `Collector.restock_balls`, and the reason is a real one rather than a
    preference. That method buys the CHEAPEST ball it can find and will fly
    off to a basic-tier shelf when the local one prices above its 400 ceiling
    -- which is exactly right for a broad dex sweep, where quantity beats
    catch rate and most of what is left is route filler. It is wrong here on
    both counts: the pillar is a Fly plus a four-leg sea crossing away from
    any Mart, so a mid-hunt shopping trip costs the whole remaining budget,
    and the targets are thrown at from FULL HP where the ball bonus is the
    only lever there is.

    Everything else is reused: `goto_map` knows how to fly to a landing and
    walk in, `clerk_cell` reads the counter clerk off the map's own object
    list, and `Mart` drives the shop through `gMartInfo` and verifies every
    purchase against the BAG COUNT and the WALLET rather than a message.
    """
    d = hunt.d
    have = hunt.balls()
    if have >= floor:
        log.info("%d balls in the pocket -- no shopping needed", have)
        return have
    log.info("%d balls and %d money -- shopping at %s", have, d.state.money(),
             mart)
    hunt.publish("shopping for balls at %s" % mart)
    if not hunt.goto_map(mart, budget=300.0):
        log.info("   could not reach %s (%s)", mart, d.last_goto_reason)
        return have
    cell = hunt.clerk_cell(mart)
    if cell is None:
        log.info("   no clerk on %s", mart)
        return have
    try:
        d.talk_to(*cell)
    except Exception as exc:  # noqa: BLE001
        log.info("   talking to the clerk raised %s", type(exc).__name__)
    for _ in range(4):
        if hunt.mart.is_open():
            break
        d.emu.run_sequence("A:4 .:40")
    if not hunt.mart.is_open():
        log.info("   the clerk did not open a shop")
        d.emu.run_sequence("B:4 .:20 B:4 .:20")
        return have
    shelf = hunt.mart.items()
    # MATCH BY ITEM ID. The shelf reports its own decoded name, which for the
    # basic ball is 'POKé BALL' -- a charmap glyph no ASCII literal equals.
    ids = {n: hunt.d.consts.items.get(n) for n in BALL_PREFERENCE}
    pick = None
    for wanted in BALL_PREFERENCE:
        want_id = ids.get(wanted)
        row = next((r for r in shelf if r["id"] == want_id), None)
        if row is not None:
            pick = (row["name"], row["price"])
            break
    if pick is None:
        log.info("   no ball on the shelf (stock: %s)",
                 ", ".join(r["name"] for r in shelf))
    else:
        name, price = pick
        # CHUNKED, because `Mart.buy` raises the quantity box one press at a
        # time and stops after MAX_PRESSES. Asking for 60 in one call settles
        # silently at 41 and the log reads like a success.
        while hunt.balls() < want:
            room = want - hunt.balls()
            qty = min(room, 30, d.state.money() // price if price else 0)
            if qty < 1:
                log.info("   %d money left -- cannot buy another %s",
                         d.state.money(), name)
                break
            if not hunt.mart.buy(name, qty):
                log.info("   %s: %s", name, hunt.mart.last_reason)
                break
    # LEAVE VERIFIED, WITH B ONLY. A blind A press in a shop list BUYS
    # things, and four presses were not enough once: the item DESCRIPTION box
    # was still up and the next `step_dir` was eaten.
    for _ in range(12):
        if not d.scene_active() and not hunt.mart.is_open():
            break
        d.emu.run_sequence("B:4 .:24")
    d.advance_scene(40000)
    now = hunt.balls()
    log.info("balls %d -> %d (money %d)", have, now, d.state.money())
    bank(d)
    return now


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True)
    ap.add_argument("--out")
    ap.add_argument("--feed", default="default")
    ap.add_argument("--minutes", type=float, default=240.0)
    #: Pacing slice. Short on purpose: 1F's stairs warp at (10,1) is a cell a
    #: pacing leg can walk THROUGH, and a warp fires on the step that enters
    #: it, so a long slice can spend its whole budget on 2F -- the floor with
    #: the cracked tiles nav cannot see. Between slices we check the map.
    ap.add_argument("--slice", type=float, default=120.0)
    ap.add_argument("--no-shop", action="store_true")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    global BANK
    BANK = a.out or a.state
    d = Driver(a.state)
    if d.at_title() and not d.resume_from_title():
        log.info("ABORT: the save never came back to the field (cb=%s)",
                 d.state.callback_name())
        return 1
    # OUTDOORS FIRST. `Driver.resume_from_title` lands in a bedroom, and Fly
    # is refused indoors -- so shopping and the voyage both need open air
    # before they can plan anything at all.
    if not to_open_air(d):
        log.info("WARNING: cannot Fly from %s -- carrying on", d.map_name())
    hunt = Hunt(d, feed_name=a.feed or None)
    deadline = time.time() + a.minutes * 60

    before = hunt._caught_count()
    want = hunt.missing_targets()
    log.info("START %s %s | dex %d caught | want %s", d.map_name(), d.pos(),
             before, sorted(want) or "nothing")
    if not want:
        log.info("both targets are already registered -- nothing to do")
        return 0
    if not a.no_shop:
        stock_balls(hunt)
    # PP, NOT HP, IS THE BINDING CONSTRAINT -- and nothing else in this script
    # restores it. The heal branch in the loop below only ever looks at the
    # lead's HP, so a lead that arrives with SURF spent and one PP on FLY
    # spends the whole hunt punching with HEADBUTT, and every turn `fight`
    # cannot take is a turn the catcher never gets. Mossdeep, where
    # `stock_balls` just shopped, has a Centre; heal there while it is free
    # rather than from the pillar, which is a Fly plus four sea legs away.
    try:
        log.info("pre-hunt heal: %s", d.heal_at_nearest_center())
    except Exception as exc:  # noqa: BLE001 - a missed heal never stops the hunt
        log.info("pre-hunt heal raised %s: %s", type(exc).__name__,
                 str(exc)[:70])
    bank(d)

    passes = 0
    #: Consecutive refused heal trips. Two is enough to conclude the Center is
    #: not reachable from here; after that the hunt carries on hurt rather
    #: than retrying a refusal on a tight loop.
    heal_fails = 0
    while time.time() < deadline:
        # LET `fight` OWN THE BATTLE. NEVER PRESS INTO ONE.
        #
        # This used to be `hunt.fight(); d.advance_scene(40000)` and that
        # second call is a bug with teeth: `advance_scene` presses A when it
        # judges the frame stalled, and A on the battle ACTION menu selects
        # FIGHT and opens the MOVE menu. So whenever `fight()` returned with
        # the battle still live -- which it does when slot 0 has no PP left
        # (PELIPPER's SURF is spent on this line) -- this drove the battle
        # into the move menu and then sat there. Measured signature: the
        # emulator ticking at full speed on SkyPillar_1F (8,12) for twenty
        # minutes, `in_battle True`, `cb BattleMainCB2`, `tasks []`,
        # `msg 'WATER'` (a MOVE name -- the move menu), every `step_dir`
        # refused with "scene-owns-input", and the ball pocket still holding
        # the exact 14 GREAT + 16 ULTRA it arrived with. The catcher was never
        # asked, so it read as "the ball throw never fires"; the throw is
        # fine, the turn never got handed to it.
        if d.in_battle():
            for _ in range(4):
                hunt.fight()
                if not d.in_battle():
                    break
            if d.in_battle():
                log.info("battle still live after 4 fight() passes "
                         "(cb=%s msg=%r) -- leaving it to the next lap",
                         d.state.callback_name(),
                         (d.state.message() or "")[:40])
            else:
                d.advance_scene(40000)
            continue
        # UNWEDGE, DON'T JUST CLOSE MENUS. `close_menus` does not clear an
        # unanswered choice box, and an open box eats every movement input --
        # after which `pace_map` asks for a step, is refused for free, and
        # asks again. The observed signature is the emulator ticking at full
        # speed with the player frozen on one tile: SkyPillar_1F (8,12) for
        # nine minutes, frames climbing 208257701 -> 208261710 in 25 seconds
        # and the position never changing. `unwedge` answers an open choice
        # NO, then presses B until `scene_active()` is False.
        share_grind.unwedge(d)
        d.close_menus()
        lead = d.state.party()[0]
        # PUSH ON WHILE TRAVELLING. The sea legs cost HP, and healing at a
        # third meant flying back to Mossdeep from Route131 -- the pillar's
        # doorstep -- and re-sailing the whole chain, forever. Only a genuinely
        # critical lead justifies giving up the distance.
        #
        # BOUNDED, AND THE BOUND IS LOAD-BEARING. `continue` on a failed heal
        # is an unconditional retry of something that just refused: the first
        # pass to actually reach the floor spent fourteen minutes printing
        # "healing (lead 85) / heal trip failed; carrying on" and never took
        # another step. A hurt lead is not a reason to stop hunting -- there
        # are five more mons behind it, the wilds here are L47-50, and a
        # whiteout costs the position but never the dex.
        floor = d.map_name() == FLOOR
        if lead.hp * (3 if floor else 6) < lead.max_hp and heal_fails < 2:
            if heal_trip(d):
                heal_fails = 0
            else:
                heal_fails += 1
                log.info("heal trip failed (%d) -- hunting on hurt",
                         heal_fails)
            continue
        if not floor:
            if not back_to_floor(d) and not climb(d):
                log.info("could not climb (at %s)", d.map_name())
                d.settle(120)
            continue
        if hunt.balls() < 1:
            log.info("out of balls at %s -- sailing back to shop", d.pos())
            if not to_open_air(d) or not d.fly_to(HEAL_TOWN):
                log.info("   could not leave the pillar; stopping")
                break
            stock_balls(hunt)
            continue
        passes += 1
        got = hunt.pace_map(min(deadline, time.time() + a.slice),
                            terrain="grass")
        want = hunt.missing_targets()
        log.info("pass %d: +%d new | dex %d | still want %s | %.0f min left",
                 passes, got, hunt._caught_count(), sorted(want) or "nothing",
                 (deadline - time.time()) / 60)
        bank(d)
        if not want:
            break

    after = hunt._caught_count()
    log.info("DONE at %s %s | dex %d -> %d | still missing %s",
             d.map_name(), d.pos(), before, after,
             sorted(hunt.missing_targets()) or "nothing")
    if a.out:
        d.save(a.out)
    return 0 if not hunt.missing_targets() else 1


if __name__ == "__main__":
    raise SystemExit(main())
