#!/usr/bin/env python
"""One chain leg that registers all three Day Care babies on ONE save.

`scripts/breed.py` proved the mechanism -- and every ROM fact quoted in its
docstring still holds, so nothing here re-derives them; this module imports it
and reuses `Bio`, `DayCareRam`, `find_parents`, `stage_party`, `hoof`,
`daycare_store`, `clear_daycare`, `collect_egg` and `dismiss_scene`
unchanged. What it adds is the three things a chain leg needs and
``breed.py --baby X`` cannot give:

1. **All three babies in one process.** `breed.py` breeds one baby per run and
   `Driver(state)` claims a live feed named after the save's stem for the
   lifetime of the process (trek.py:_autofeed), so running it three times in
   one interpreter is a feed clash and three subprocesses is three cold boots.
   `do_baby` is therefore a function, called in a loop, and the save is
   written after EVERY baby -- an interrupted leg keeps what it hatched.

2. **Idempotence.** Every baby is gated on `DexTarget.dex_flags`, read from
   the live game, so a re-run of a finished leg does nothing and exits 0. A
   pair already sitting in the Day Care is adopted rather than evicted.

3. **The AZURILL equip, which is what actually blocked it.**
   `AlterEggSpeciesWithIncenseItem` (pret/src/daycare.c:602-622) rewrites
   AZURILL to MARILL unless a Day Care parent holds SEA INCENSE, and
   `breed.give_held` could not put it on one: it drives the FIELD party menu,
   reaches the bag, and the A press on the item row hands nothing over. Its
   documented last resort -- `Teacher.give_to_mon(item, mon)` -- was called
   with the `Mon` NAMEDTUPLE, and that function matches `str(mon).upper()`
   against a nickname or a species name (teaching.py:733-741), so a namedtuple
   repr can never match and it failed with "no party member matches" for a mon
   that was plainly in the party. `equip` below passes the SPECIES NAME and
   first reduces the party to exactly two, which is the one shape the bag's
   party picker has ever been steered correctly in (share_grind.py:248-262):
   with two mons the OAM cursor read in `_pick_party_member` only has to move
   one row. It then judges the result on `held_item` and, if the item landed
   on the escort instead, takes it straight back and retries.

Order is AZURILL first, deliberately. It is the only baby of the three that
is not already registered somewhere else in this project, so it gets the
freshest party and the first attempt at the bag; and because a baby that
fails does NOT abort the leg, the two proven ones still run after it.
"""

import argparse
import logging
import sys
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from pokeagent import cconst, paths                       # noqa: E402
from pokeagent.dex import DexTarget                       # noqa: E402
from pokeagent.menus import Menus                         # noqa: E402
from pokeagent.storage import Storage                     # noqa: E402
from pokeagent.teaching import ITEMS_POCKET, Teacher      # noqa: E402
from pokeagent.trek import Driver                         # noqa: E402
import breed as B                                         # noqa: E402
from share_grind import unwedge                           # noqa: E402

log = logging.getLogger("breed2")

#: AZURILL first: see the module docstring.
ORDER = ["AZURILL", "IGGLYBUFF", "PICHU"]


# --------------------------------------------------------------------------
# small readers
# --------------------------------------------------------------------------

def locate(d, personality):
    """Where a mon is NOW, as `breed.find_parents` would report it.

    ``(None, party_index, Mon)`` for a party member, ``(box, slot, Mon)`` for
    a boxed one, None if it is in neither (in the Day Care, most likely).
    Every party operation renumbers both, so nothing here is ever cached: a
    stale ``(box, slot)`` handed to `Storage.withdraw` pulls out a NEIGHBOUR.
    """
    for i, m in enumerate(d.state.party()):
        if m.species and m.personality == personality:
            return (None, i, m)
    for box, slot, m in B.boxed(d):
        if m.personality == personality:
            return (box, slot, m)
    return None


def taxi_index(d):
    """The party index that must never be benched: the FLY/SURF carrier.

    `breed._keepers` returns a SET, which is the right answer for staging but
    useless for "reduce the party to exactly two" -- that needs one mon, and
    it has to be the one that can still both fly to a Centre and cross water,
    or the walk back to Route 117 strands the leg.
    """
    best = None
    for i, m in enumerate(d.state.party()):
        if not m.species or m.is_egg:
            continue
        moves = {d.names.move(mv).upper().replace(" ", "")
                 for mv in m.moves}
        score = 2 * ("FLY" in moves) + ("SURF" in moves)
        if score and (best is None or score > best[0]):
            best = (score, i)
    return best[1] if best else 0


def holders(d, ctx, item_id):
    """Everything holding `item_id`, as ``(where, Mon)`` pairs."""
    out = []
    for i, m in enumerate(d.state.party()):
        if m.species and m.held_item == item_id:
            out.append((("party", i), m))
    for box, slot, m in B.boxed(d):
        if m.held_item == item_id:
            out.append((("box", box, slot), m))
    for i in range(ctx.dc.slots):
        m = ctx.dc.mon(i)
        if m and m.held_item == item_id:
            out.append((("daycare", i), m))
    return out


def surface(d, tries=6) -> bool:
    """Come up out of MAP_TYPE_UNDERWATER.

    A chain leg inherits the last leg's position and the `underwater` leg ends
    on Underwater1. Nothing in `breed.to_pc` can recover from that:
    `Overworld_MapTypeAllowsTeleportAndFly` refuses Fly on
    MAP_TYPE_UNDERWATER, and `flight.step_outside` -- which knows how to leave
    a BUILDING -- returns False without moving, so `to_pc` fell through to
    `heal_at_nearest_center`, which spent five minutes at 99.6% CPU trying to
    route a land walk out of the sea floor. Measured, on this fork:
    "fly: indoors -- Underwater1 is MAP_TYPE_UNDERWATER, which
    Overworld_MapTypeAllowsTeleportAndFly refuses", twice, then silence.

    Surfacing is `Driver.dive` (B underwater, trek.py:1740-1747) and it acts
    on the tile the player is STANDING on, so a refusal of
    "no-surfacing-here" -- seaweed, which is most of Underwater1 -- is
    answered by walking to a cell `nav.dive_gates(map, "emerge")` says is
    surfacable, not by pressing again.
    """
    for _ in range(tries):
        if not d.underwater():
            return True
        if d.dive():
            log.info("  surfaced -> %s %s", d.map_name(), d.pos())
            continue
        why = d.last_field_reason
        log.info("  surface refused at %s %s: %s", d.map_name(), d.pos(), why)
        if why not in ("no-surfacing-here", "no-cell"):
            return False
        x0, y0 = d.pos()
        gates = sorted(d.nav.dive_gates(d.map_name(), "emerge"),
                       key=lambda c: abs(c[0] - x0) + abs(c[1] - y0))
        if not gates:
            return False
        moved = False
        for x, y in gates[:8]:
            if (x, y) == (x0, y0):
                continue
            if d.goto(x, y):
                moved = True
                break
        if not moved:
            log.info("  no reachable surfacable cell (%d candidates): %s",
                     len(gates), d.last_goto_reason)
            return False
    return not d.underwater()


#: The Centre the PC work is done in. It is one map from Route 117, so the
#: walk to the Day Care afterwards is a single `travel` leg.
HOME_PC = "MauvilleCity_PokemonCenter_1F"
HOME_CITY = "MauvilleCity"


def to_mauville(d) -> bool:
    """Get onto the Mauville landmass, from anywhere.

    `Overworld_MapTypeAllowsTeleportAndFly` refuses indoors and underwater,
    so this surfaces first and lets `fly_to` do its own `step_outside`.
    """
    if d.map_name().startswith(HOME_CITY) or d.map_name() == "Route117":
        return True
    if d.underwater() and not surface(d):
        log.info("  still underwater at %s %s", d.map_name(), d.pos())
        return False
    try:
        return bool(d.fly_to(HOME_CITY))
    except Exception as exc:                    # noqa: BLE001
        log.info("  fly %s: %s", HOME_CITY, str(exc)[:90])
        return False


def reach_pc(d) -> bool:
    """Stand in MAUVILLE's Centre -- not merely in *a* Centre.

    `breed.to_pc` returns True the moment the map name ends
    `PokemonCenter_1F`, and that is wrong for this leg: a chain hands it
    whatever Centre the last leg healed at, and from
    MossdeepCity_PokemonCenter_1F -- which is where the re-banked canonical
    save now sits -- there is no land route to the Day Care at all. Measured:
    the PC work completed at Mossdeep and then "travel Route117: no walkable
    route from MossdeepCity to Route117", losing the whole baby.
    """
    if d.map_name() == HOME_PC:
        return True
    to_mauville(d)
    return B.to_pc(d)


def to_daycare(d) -> bool:
    """`breed.to_daycare`, but it may be starting an island away."""
    if d.map_name() == B.DAYCARE_MAP:
        return True
    to_mauville(d)
    return B.to_daycare(d)


def bank(d, path, why) -> bool:
    """Write the save, but only from a clean field state.

    A savestate taken with a message box up is a WEDGED save for every leg
    that inherits it, which is exactly how one canonical line lost 17 minutes
    to an unanswered naming keyboard (breed.dismiss_scene).
    """
    unwedge(d)
    d.advance_scene(40_000)
    if not B.in_field(d) or d.scene_active():
        log.info("NOT banking after %s: cb=%s tasks=%s", why,
                 d.state.callback_name(), d.state.tasks())
        return False
    d.save(path)
    log.info("banked %s after %s", path, why)
    return True


# --------------------------------------------------------------------------
# the party
# --------------------------------------------------------------------------

def bench_to(d, ctx, limit, protect):
    """Deposit party members until at most `limit` remain.

    `protect` is a set of personalities that stay. Eggs are never deposited
    (the PC refuses them) and the lowest level goes first, so a just-hatched
    baby -- already registered, worth nothing to the party -- is always the
    first to go and a field-move carrier is only touched if it is all that is
    left.
    """
    st = Storage(d)
    for _ in range(8):
        alive = [(i, m) for i, m in enumerate(d.state.party()) if m.species]
        if len(alive) <= limit:
            return True
        pool = [(m.level or 0, i, m) for i, m in alive
                if m.personality not in protect and not m.is_egg]
        if not pool:
            log.info("  nothing left to bench (party %s)",
                     B.show_party(d, ctx.bio))
            return False
        pool.sort()
        _lvl, idx, mon = pool[0]
        name = d.names.species(mon.species)
        if not st.deposit(idx):
            log.info("  could not bench %s: %s", name, st.last_reason)
            return False
        log.info("  benched %s -> PC (party %s)", name,
                 B.show_party(d, ctx.bio))
    return len([m for m in d.state.party() if m.species]) <= limit


def tidy_daycare(d, ctx) -> bool:
    """Leave the Day Care EMPTY and with no egg pending.

    Not cosmetic. Two things a chain leg must not hand its successors:

    * A PENDING EGG puts `GetDaycareState` at 1, and at state 1 the Old
      Woman's entire script is one message and a release
      (``Route117_EventScript_1B2407``) -- no deposit branch, no withdraw
      branch. Any later leg that wants the Day Care is locked out, and this
      leg's own `clear_daycare` only recovers because it knows to go and
      refuse the egg first.
    * The LAST PAIR IS STILL IN THERE, and a mon in the Day Care is in
      neither the party nor `Storage.boxed()`. The final pair is PIKACHU x
      PIKACHU, which is exactly what a THUNDERSTONE leg has to find to
      register RAICHU; leaving them invisible would read as "no PIKACHU
      owned".

    Party room is made in one PC trip, before walking to Route 117, because
    `..._EventScript_1B2469` refuses to hand a mon back with six in the party
    and each retrieval needs its own slot.
    """
    need = len(ctx.dc.occupants())
    if not need:
        return True
    live = len([m for m in d.state.party() if m.species])
    if live > 6 - need:
        if not reach_pc(d):
            log.info("tidy: no Centre reachable to make room")
            return False
        if not bench_to(d, ctx, 6 - need, set()):
            return False
    if not to_daycare(d):
        log.info("tidy: could not reach %s", B.DAYCARE_MAP)
        return False
    # `reject_egg` reads the day care through this module global, which
    # `clear_daycare` is normally what sets.
    B._pending_dc = ctx.dc
    for _ in range(ctx.dc.slots + 2):
        inside = ctx.dc.occupants()
        if not inside:
            log.info("tidy: day care is empty and no egg is pending")
            return True
        if ctx.dc.pending():
            log.info("tidy: refusing the pending egg first (state 1 has no "
                     "withdraw branch)")
            if not B.reject_egg(d, ctx.menus) or not to_daycare(d):
                return False
        if not B.daycare_retrieve(d, ctx.dc, ctx.menus,
                                  inside[0].personality):
            log.info("tidy: could not take %s back",
                     d.names.species(inside[0].species))
            return False
    return not ctx.dc.occupants()


def equip(d, ctx, mother_p, item, tries=3) -> bool:
    """Put `item` on the mon with `mother_p`, judged on ITS held_item.

    Assumes a Pokemon Center (the PC work needs one). The party is squeezed
    down to [escort, mother] first -- see the module docstring for why that
    specific shape -- and the escort is the FLY/SURF carrier so the leg can
    still travel afterwards.
    """
    item_id = ctx.teacher._item_id(item)
    if not item_id:
        log.info("  %r is not an item this ROM knows", item)
        return False

    def held():
        hit = locate(d, mother_p)
        return hit[2].held_item if hit else None

    if held() == item_id:
        return True

    # RECLAIM IT FIRST. `give_to_mon` refuses outright when the item is not
    # in the ITEMS pocket, and giving it away is what empties the pocket -- so
    # a half-finished earlier attempt that parked it on the escort would make
    # every retry fail with "not in the ITEMS pocket" while the item sat two
    # slots away.
    for where, mon in holders(d, ctx, item_id):
        if mon.personality == mother_p:
            return True
        if where[0] == "daycare":
            log.info("  %s is already on a Day Care mon (%s)", item,
                     d.names.species(mon.species))
            continue
        if where[0] == "box":
            log.info("  %s is on boxed %s -- withdrawing it to take it back",
                     item, d.names.species(mon.species))
            if not bench_to(d, ctx, 5, {mother_p}):
                return False
            Storage(d).withdraw(where[1], where[2])
            mon = locate(d, mon.personality)[2] if locate(
                d, mon.personality) else mon
        idx = next((i for i, m in enumerate(d.state.party())
                    if m.species and m.personality == mon.personality), None)
        if idx is not None:
            log.info("  taking %s back off %s (party slot %d)", item,
                     d.names.species(mon.species), idx)
            ctx.teacher.take_from_mon(idx)

    # The mother has to be IN the party, and she has to be one of two.
    st = Storage(d)
    spot = locate(d, mother_p)
    if spot is None:
        log.info("  the mother is in neither the party nor a box -- she is "
                 "in the Day Care, where nothing can be given to her")
        return False
    if spot[0] is not None:
        escort = taxi_index(d)
        keep = {d.state.party()[escort].personality}
        if not bench_to(d, ctx, 1, keep):
            return False
        spot = locate(d, mother_p)
        if spot is None or spot[0] is None:
            log.info("  the mother is no longer in a box (spot %s)", spot)
            return False
        if not st.withdraw(spot[0], spot[1]):
            log.info("  could not withdraw the mother: %s", st.last_reason)
            return False
    escort = taxi_index(d)
    keep = {d.state.party()[escort].personality, mother_p}
    if not bench_to(d, ctx, 2, keep):
        return False
    log.info("  party squeezed to %s for the give",
             B.show_party(d, ctx.bio))

    mon = locate(d, mother_p)[2]
    name = d.names.species(mon.species)
    for attempt in range(tries):
        if held() == item_id:
            return True
        idx = next(i for i, m in enumerate(d.state.party())
                   if m.species and m.personality == mother_p)
        unwedge(d)
        d.close_menus()
        d.settle(400)
        # SPECIES NAME, NOT THE MON. `give_to_mon` matches
        # `str(mon).upper()` against the nickname or the species name
        # (teaching.py:733-741); `breed.give_held` passed the namedtuple, so
        # it could only ever report "no party member matches".
        try:
            ctx.teacher.give_to_mon(item, name)
        except Exception as exc:                # noqa: BLE001
            log.info("  give_to_mon: %s", str(exc)[:100])
        d.close_menus()
        d.settle(400)
        if held() == item_id:
            log.info("  %s now holds %s (bag give)", name, item)
            return True
        log.info("  give attempt %d: %s holds %s (%s)", attempt + 1, name,
                 d.names.item(held()) if held() else "nothing",
                 getattr(ctx.teacher, "last_reason", "?"))
        # It may have landed on the escort. Take it back rather than leave
        # the only copy in the game on the wrong mon.
        for where, other in holders(d, ctx, item_id):
            if where[0] == "party" and other.personality != mother_p:
                log.info("  it went to %s -- taking it back",
                         d.names.species(other.species))
                ctx.teacher.take_from_mon(where[1])
        # Second route: the FIELD party menu, which fails differently.
        try:
            ctx.teacher.give_from_field(idx, item)
        except Exception as exc:                # noqa: BLE001
            log.info("  give_from_field: %s", str(exc)[:100])
        d.close_menus()
        d.settle(400)
        if held() == item_id:
            log.info("  %s now holds %s (field give)", name, item)
            return True
        for where, other in holders(d, ctx, item_id):
            if where[0] == "party" and other.personality != mother_p:
                ctx.teacher.take_from_mon(where[1])
    return held() == item_id


# --------------------------------------------------------------------------
# one baby
# --------------------------------------------------------------------------

def do_baby(d, ctx, baby, a) -> tuple[bool, str]:
    """Register `baby`. Returns ``(got_it, why)``; never raises for a miss."""
    baby_id = B.species_id(d, baby)
    natdex = ctx.evo.natdex(baby_id)
    caught, _seen = ctx.target.dex_flags(d.state)
    if natdex in caught:
        return True, "already registered"

    dc, bio, menus = ctx.dc, ctx.bio, ctx.menus

    # ---- parents: a pair already in the Day Care wins -------------------
    inside = dc.occupants()
    mother = father = None
    why = ""
    if len(inside) == dc.slots and bio.compat(inside[0], inside[1]):
        mum = next((m for m in inside if bio.gender(m) == bio.FEMALE), None)
        if mum is not None and B.egg_species(ctx.evo, mum.species) == baby_id:
            mother = (None, None, mum)
            father = (None, None, next(m for m in inside
                                       if m.personality != mum.personality))
            why = f"already deposited, compat {bio.compat(*inside)}"
    if mother is None:
        mother, father, why = B.find_parents(d, bio, ctx.evo, baby)
    if mother is None and baby in B.ACQUIRE and not a.no_acquire:
        log.info("[%s] no mother owned: hunting a female %s", baby,
                 B.ACQUIRE[baby])
        if B.hunt_female(d, bio, menus, B.ACQUIRE[baby],
                         budget_s=a.hunt_budget):
            mother, father, why = B.find_parents(d, bio, ctx.evo, baby)
    if mother is None:
        return False, why
    mother_p, father_p = mother[2].personality, father[2].personality
    keep = {mother_p, father_p}
    strays = [m for m in dc.occupants() if m.personality not in keep]
    log.info("[%s] parents: %s%s", baby, why,
             f" | {len(strays)} stray(s) to evict" if strays else "")

    incense = B.INCENSE.get(baby)
    if incense:
        item_id = ctx.teacher._item_id(incense)
        in_bag = any(iid == item_id
                     for _s, iid, _q in ctx.teacher.pocket_items(
                         ITEMS_POCKET))
        have = holders(d, ctx, item_id)
        if not in_bag and not have:
            return False, (
                f"{incense} is in neither the ITEMS pocket nor on any mon -- "
                f"without it the egg hatches as MARILL "
                f"(pret/src/daycare.c:602-622)")
        log.info("[%s] %s: bag=%s held_by=%s", baby, incense, in_bag,
                 [(w, d.names.species(m.species)) for w, m in have])

    # ---- stage and deposit ---------------------------------------------
    occ = {m.personality for m in dc.occupants()}
    staged = mother_p in occ and father_p in occ
    if not staged:
        if not reach_pc(d):
            return False, f"no Pokemon Center reachable (at {d.map_name()})"
        log.info("[%s] PC at %s %s | party %s", baby, d.map_name(), d.pos(),
                 B.show_party(d, bio))
        if incense:
            item_id = ctx.teacher._item_id(incense)
            if not any(m.held_item == item_id for m in dc.occupants()):
                if not equip(d, ctx, mother_p, incense):
                    return False, f"could not put {incense} on the mother"
        # A parent ALREADY in the Day Care is reported as neither party nor
        # box; `stage_party` reads `p[0] is None` as "in the party, leave it
        # alone", which is the right behaviour for one too.
        ok, reason = B.stage_party(
            d, bio,
            locate(d, mother_p) or (None, None, mother[2]),
            locate(d, father_p) or (None, None, father[2]),
            keep_free=len(strays))
        if not ok:
            return False, reason
        log.info("[%s] staged %s", baby, B.show_party(d, bio))
        if not to_daycare(d):
            return False, f"could not reach {B.DAYCARE_MAP}"
        if strays and not B.clear_daycare(d, dc, menus, keep):
            return False, "could not evict the previous pair"
        for personality in (mother_p, father_p):
            if personality in {m.personality for m in dc.occupants()}:
                continue
            if not B.daycare_store(d, dc, menus, personality):
                spot = locate(d, personality)
                return False, ("could not deposit "
                               f"{d.names.species(spot[2].species)}"
                               if spot else "could not deposit a parent")

    # ---- the gates, all read from RAM ----------------------------------
    inside = dc.occupants()
    if len(inside) != dc.slots:
        return False, f"day care holds {len(inside)}, needs {dc.slots}"
    score = bio.compat(inside[0], inside[1])
    log.info("[%s] day care: %s | compat %d -> ~%d steps per egg", baby,
             [f"{d.names.species(m.species)}{bio.sex(m)}"
              f"{'+' + d.names.item(m.held_item) if m.held_item else ''}"
              for m in inside], score, int(256 * 100 / max(score, 1)))
    if not score:
        return False, "compatibility 0 -- no egg will ever be offered"
    mum = next((m for m in inside if bio.gender(m) == bio.FEMALE), None)
    if mum is None:
        return False, "neither day care mon is female"
    predicted = B.egg_species(ctx.evo, mum.species)
    if predicted != baby_id:
        return False, (f"this pair lays {d.names.species(predicted)}, "
                       f"not {baby}")
    if incense:
        item_id = ctx.teacher._item_id(incense)
        if not any(m.held_item == item_id for m in inside):
            return False, (f"no day care parent holds {incense} -- the egg "
                           f"would hatch as MARILL, not {baby}")
        log.info("[%s] %s is on a day care parent -- the egg will be %s",
                 baby, incense, baby)

    # ---- the egg --------------------------------------------------------
    egg_steps = 0
    if not any(m.is_egg for m in d.state.party()):
        # `collect_egg` is refused outright with six in the party
        # (CalculatePlayerPartyCount / compare 6).
        if len([m for m in d.state.party() if m.species]) >= 6:
            if not reach_pc(d):
                return False, "party full and no Centre reachable for the egg"
            if not bench_to(d, ctx, 5, keep):
                return False, "no room in the party for the egg"
        if dc.pending():
            log.info("[%s] an egg is already pending", baby)
        else:
            if not to_daycare(d):
                return False, "could not reach the walking map"
            deadline = time.time() + a.egg_budget
            while not dc.pending():
                if time.time() >= deadline:
                    break
                took, stop = B.hoof(d, dc, 256, f"for the {baby} egg",
                                    budget_s=max(30.0,
                                                 deadline - time.time()))
                egg_steps += took
                if stop == "scene":
                    d.advance_scene(40_000)
                    unwedge(d)
            if not dc.pending():
                return False, (f"no egg after {egg_steps} real steps "
                               f"(compat {score})")
            log.info("[%s] EGG PENDING after %d real steps (~%d expected at "
                     "compat %d)", baby, egg_steps,
                     int(256 * 100 / score), score)
        if not B.leave_daycare(d):
            return False, "could not get back onto Route117"
        d.sync_grid()
        if not B.collect_egg(d, dc, menus):
            return False, "the DAY-CARE MAN would not hand the egg over"

    egg = next((m for m in d.state.party() if m.is_egg), None)
    log.info("[%s] egg in the party (cycles left %d) | party %s", baby,
             egg.friendship, B.show_party(d, bio))

    # ---- hatch it -------------------------------------------------------
    if not to_daycare(d):
        return False, "could not reach the walking map to hatch"
    hatch_steps, stop = B.hoof(d, dc, B.HATCH_STEPS, f"to hatch the {baby}",
                               budget_s=a.hatch_budget)
    if stop == "scene" or not any(m.is_egg for m in d.state.party()):
        log.info("[%s] hatch scene at %d real steps", baby, hatch_steps)
        if not B.dismiss_scene(d, menus):
            return False, "the hatch scene never released"
    d.advance_scene(40_000)
    unwedge(d)

    caught_now, _ = ctx.target.dex_flags(d.state)
    if natdex not in caught_now:
        return False, (f"hatched at {hatch_steps} steps but #{natdex} is "
                       f"still not flagged (party {B.show_party(d, bio)})")
    log.info("[%s] REGISTERED | egg %d steps, hatch %d steps (predicted %d) "
             "| party %s", baby, egg_steps, hatch_steps, B.HATCH_STEPS,
             B.show_party(d, bio))
    ctx.steps[baby] = (egg_steps, hatch_steps)
    return True, f"egg {egg_steps} steps, hatch {hatch_steps} steps"


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--state", required=True,
                    help="savestate to MUTATE IN PLACE (chain leg contract)")
    ap.add_argument("--babies", default=",".join(ORDER),
                    help="comma-separated subset, in order")
    ap.add_argument("--egg-budget", type=float, default=900.0)
    ap.add_argument("--hatch-budget", type=float, default=900.0)
    ap.add_argument("--hunt-budget", type=float, default=1800.0)
    ap.add_argument("--no-acquire", action="store_true")
    ap.add_argument("--no-save", action="store_true")
    ap.add_argument("--no-restore", action="store_true",
                    help="leave the squeezed party as it is")
    ap.add_argument("--tidy-only", action="store_true",
                    help="skip breeding: just empty the Day Care and refuse "
                         "any pending egg, then bank")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s breed2 %(message)s")

    want = [n.strip().upper() for n in a.babies.split(",") if n.strip()]
    bad = [n for n in want if n not in ORDER]
    if bad:
        raise SystemExit(f"unknown babies {bad}; known {ORDER}")

    d = Driver(a.state)
    if not B.leave_title(d):
        log.info("REFUSING: not in the field (cb %s)",
                 d.state.callback_name())
        return 1
    d.advance_scene(40_000)
    unwedge(d)
    if d.underwater():
        log.info("inherited an UNDERWATER position -- surfacing first")
        surface(d)

    slots = cconst.parse_defines(
        str(paths.CONSTANTS / "global.h"))["DAYCARE_MON_COUNT"]
    ctx = SimpleNamespace()
    ctx.dc = B.DayCareRam(d.emu, slots)
    ctx.bio = B.Bio(d)
    ctx.target = DexTarget(d.emu, d.names, d.consts, d.nav, spec=d.spec)
    ctx.evo = ctx.target.evolutions
    ctx.menus = Menus(d.emu, d.state)
    ctx.teacher = Teacher(d)
    ctx.steps = {}

    caught0, _ = ctx.target.dex_flags(d.state)
    original = [m.personality for m in d.state.party() if m.species]
    log.info("start %s %s | dex %d | party %s", d.map_name(), d.pos(),
             len(caught0), B.show_party(d, ctx.bio))
    log.info("day care holds %s (steps %d, pending %#x)",
             [d.names.species(m.species) for m in ctx.dc.occupants()],
             ctx.dc.steps(0), ctx.dc.pending())

    todo = [b for b in want
            if ctx.evo.natdex(B.species_id(d, b)) not in caught0]
    tidied = True
    if a.tidy_only:
        # FALLS THROUGH to the restore phase: emptying the Day Care puts the
        # last pair back INTO the party, so returning here would hand the
        # next leg a party of leftovers.
        todo = []
        tidied = tidy_daycare(d, ctx)
        log.info("tidy-only: day care now %s (pending %#x)",
                 [d.names.species(m.species) for m in ctx.dc.occupants()],
                 ctx.dc.pending())
        if not a.no_save:
            bank(d, a.state, "tidy")
    elif not todo:
        log.info("all of %s are already registered -- nothing to do", want)
        return 0
    else:
        log.info("to breed: %s", todo)

    results = {}
    for baby in todo:
        t0 = time.time()
        try:
            got, why = do_baby(d, ctx, baby, a)
        except Exception as exc:                # noqa: BLE001
            log.exception("[%s] blew up: %s", baby, exc)
            got, why = False, f"exception: {type(exc).__name__}: {exc}"
        results[baby] = (got, why)
        log.info("=== %s %s in %.0fs: %s", baby,
                 "REGISTERED" if got else "MISSED", time.time() - t0, why)
        # BANK AFTER EVERY BABY, pass or fail: the walking is the expensive
        # part and a later baby's failure must not cost an earlier one.
        if not a.no_save:
            bank(d, a.state, baby)

    # ---- hand the Day Care back the way it was found --------------------
    if todo:
        tidied = tidy_daycare(d, ctx)
        if not tidied:
            log.info("WARNING: the day care still holds %s (pending %#x) -- "
                     "a later leg cannot use the clerk while an egg is "
                     "pending",
                     [d.names.species(m.species) for m in ctx.dc.occupants()],
                     ctx.dc.pending())
        elif not a.no_save:
            bank(d, a.state, "day care tidy")

    # ---- put the party back --------------------------------------------
    # The equip squeezes the party to two, and every later leg of a chain
    # inherits whatever is left. Best effort only, and never at the cost of
    # what is already banked: a failure here is logged and ignored.
    if not a.no_restore and original:
        try:
            missing = [p for p in original if locate(d, p)
                       and locate(d, p)[0] is not None]
            if missing and reach_pc(d):
                st = Storage(d)
                for personality in missing:
                    live = [m for m in d.state.party() if m.species]
                    if len(live) >= 6:
                        if not bench_to(d, ctx, 5, set(original)):
                            break
                    spot = locate(d, personality)
                    if spot is None or spot[0] is None:
                        continue
                    if not st.withdraw(spot[0], spot[1]):
                        log.info("restore: %s refused: %s",
                                 d.names.species(spot[2].species),
                                 st.last_reason)
                log.info("restored party %s", B.show_party(d, ctx.bio))
                if not a.no_save:
                    bank(d, a.state, "party restore")
        except Exception as exc:                # noqa: BLE001
            log.info("restore skipped: %s", str(exc)[:120])

    caught1, _ = ctx.target.dex_flags(d.state)
    log.info("RESULT dex %d -> %d", len(caught0), len(caught1))
    for n in sorted(caught1 - caught0):
        log.info("  NEW natdex #%d %s", n, d.names.species(
            ctx.evo.species_of_natdex(n) or 0))
    for baby, (got, why) in results.items():
        egg, hatch = ctx.steps.get(baby, (0, 0))
        log.info("  %-10s %s  %s%s", baby, "OK  " if got else "MISS", why,
                 f" [{egg}+{hatch} real steps]" if got and hatch else "")
    return 0 if (tidied and all(got for got, _ in results.values())) else 1


if __name__ == "__main__":
    raise SystemExit(main())
