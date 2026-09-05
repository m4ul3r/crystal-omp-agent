#!/usr/bin/env python
"""Bench-grind a pre-evolution on the Elite Four with the EXP. SHARE.

The proven fast path, and it needs no battle trickery at all. The ROM
(`battle_script_commands.c:3381-3441`) pays the share holder
`expYield * level / 7 / 2 / viaExpShare` for EVERY knockout, whether or not it
was sent out -- it only has to be alive. With one share and a level-100 lead
that is HALF of every Elite Four mon, about 25 knockouts a lap, which took
GOLDEEN from L22 to L28 in a single pass earlier in this run.

Two things had to be fixed before it could be used:

* the share was welded to NINJASK, because a mon deposited while holding an
  item empties it from the bag. `Teacher.take_from_mon` now unequips it -- the
  per-mon popup is an ordinary `InitMenu` menu, so `select_index` drives it,
  and `_pick_party_member` already opens it (the extra A press was selecting
  CUT and answering "There's nothing to CUT.").
* the holder must NOT lead. A L25 target in front of Sidney's L46 MIGHTYENA is
  knocked out on turn one, and `viaSentIn`/the exp loop both skip a fainted
  mon (`:3361-3364`, `:3436`), so it earns nothing. Benched and never sent
  out, it takes no damage and still collects.
"""
import argparse, logging, sys
sys.path.insert(0, ".")
sys.path.insert(0, "scripts")

from pokeagent.trek import Driver
from pokeagent.dex import DexTarget
from pokeagent.storage import Storage
from pokeagent.teaching import Teacher

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("share")

SHARE = 182
SHARE_NAME = "EXP SHARE"   # no period: "EXP. SHARE" resolves to None in this ROM
LEAD = "PELIPPER"                 # the L100; it earns nothing, so it sweeps


def party(d):
    return [m for m in d.state.party() if not m.is_egg]


def spn(d, m):
    return d.names.species(m.species).upper()


def holder(d):
    for i, m in enumerate(d.state.party()):
        if not m.is_egg and m.held_item == SHARE:
            return i
    return None


def unwedge(d) -> bool:
    """Clear whatever dialogue or menu is eating input, before moving.

    A gauntlet killed by its own timeout banks a WEDGED save: the canonical
    line was found at Corridor4 with the bag still open on a REVIVE
    description (task `sub_80A5414`, `choice_open` True), and in that state
    `step_outside` hangs forever -- staging then burns its whole budget and
    every lap fails without the dex moving. An open menu eats all movement
    input, so this has to run before anything else tries to walk.
    """
    from pokeagent.menus import Menus

    # THE TITLE SCREEN IS THE OTHER WAY THIS WEDGES, and it does not look
    # like a wedge at all: after a Champion win the credits roll and the game
    # soft-resets, but SaveBlock RAM survives so the map, party and dex all
    # read normally while the player simply cannot move.
    if d.at_title():
        log.info("  on the title screen -- taking CONTINUE")
        if not d.resume_from_title():
            log.info("  could not get back into the field from the title")
            return False
    if not d.scene_active():
        return True
    menus = Menus(d.emu, d.state)
    try:
        if d.choice_open():
            menus.resolve_choice("NO")
            d.settle(600)
    except Exception:  # noqa: BLE001
        pass
    for _ in range(14):
        if not d.scene_active():
            return True
        d.emu.run_sequence("B:6 .:60")
        d.settle(400)
        d.close_menus()
        d.settle(300)
    if d.scene_active():
        log.info("  still wedged: %r", (d.state.message() or "")[:70])
        return False
    return True


def to_center(d):
    unwedge(d)
    if d.map_name().endswith("PokemonCenter_1F"):
        return True
    try:
        d.heal_at_nearest_center()
    except Exception as e:  # noqa: BLE001
        log.info("  heal: %s", str(e)[:80])
    if d.map_name().endswith("PokemonCenter_1F"):
        return True
    # WALK OUT THROUGH THE MAP'S OWN WARPS. `step_outside` cannot leave an
    # Elite Four member's room -- the plateau interior is a one-way chain, so
    # from PhoebesRoom it answered "no approach to warp (6,13)" and staging
    # gave up with "no Centre reachable". Fly is refused indoors
    # (MAP_TYPE_INDOOR), so the only way out is to take warps until the map
    # is somewhere Fly accepts.
    for _ in range(12):
        if d.flight.flyable_here():
            break
        before = d.map_name()
        # SYNC THE LIVE GRID FIRST. An Elite Four room's exit door is written
        # by `setmetatile` DURING the victory animation, so the static .blk
        # still reads it as a wall and `take_warp` answers "no approach to
        # warp (6,13)" on a door that is standing open.
        try:
            d.sync_grid()
        except Exception:  # noqa: BLE001
            pass
        moved = False
        for w in d.nav.exits(before):
            if w.get("kind") != "warp":
                continue
            try:
                if d.take_warp(w["x"], w["y"]) and d.map_name() != before:
                    moved = True
                    break
            except Exception:  # noqa: BLE001
                continue
        if not moved:
            break

    # WALK OUT OF THE LEAGUE, HOWEVER DEEP IT IS. The plateau interior is a
    # one-way chain of rooms and corridors; a run stopped mid-gauntlet leaves
    # the save at Corridor4, and four step_outside attempts is not enough to
    # reach open ground -- staging then burned its whole 8-minute budget and
    # every lap failed with rc=1 while the dex sat still.
    for _ in range(14):
        if d.flight.flyable_here():
            break
        before = d.map_name()
        try:
            d.flight.step_outside()
        except Exception as e:  # noqa: BLE001
            log.info("  step_outside: %s", str(e)[:60])
            break
        if d.map_name() == before:
            break
    try:
        d.fly_to("MauvilleCity")
        d.heal_at_nearest_center()
    except Exception as e:  # noqa: BLE001
        log.info("  fly/heal: %s", str(e)[:80])
    return d.map_name().endswith("PokemonCenter_1F")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True)
    ap.add_argument("--target", required=True, help="species to hand the share")
    a = ap.parse_args()
    target = a.target.strip().upper()

    d = Driver(a.state)
    d.advance_scene(40_000)
    dex = DexTarget(d.emu, d.names, d.consts, d.nav, spec=d.spec)
    log.info("start %s | %s", d.map_name(), dex.summary(d.state).split(";")[0])

    if not to_center(d):
        log.info("no Centre reachable (at %s)", d.map_name())
        return 1
    st, t = Storage(d), Teacher(d)

    # SHRINK THE PARTY BEFORE TOUCHING THE PICKER. Its index does not map to
    # the party index, so every extra mon is another wrong row the take can
    # land on. Keeping only the lead and the current holder leaves two.
    hh = holder(d)
    if hh is not None:
        keep = {spn(d, d.state.party()[hh]), LEAD}
        for _ in range(8):
            p0 = party(d)
            if len(p0) <= 2:
                break
            drop = next((i for i, m in enumerate(p0) if spn(d, m) not in keep),
                        None)
            if drop is None or not st.deposit(drop):
                break

    # 1. free the share from whoever is wearing it
    h = holder(d)
    if h is not None:
        log.info("share is on slot %d (%s) -- taking it", h,
                 spn(d, d.state.party()[h]))
        if not t.take_from_mon(h):
            log.info("could not unequip: %s", getattr(t, "last_reason", "?"))
            return 1
        log.info("share is in the bag; pocket0 ids now: %s",
                 [r[1] for r in t.pocket_items(0)])

    # 2. make sure the target and the L100 lead are both in the party
    fresh = DexTarget(d.emu, d.names, d.consts, d.nav, spec=d.spec)
    for species in (LEAD, target):
        if any(spn(d, m) == species for m in party(d)):
            continue
        hit = next(((s, mo) for s, mo in fresh.boxed()
                    if d.names.species(mo.species).upper() == species), None)
        if hit is None:
            log.info("%s is in neither party nor boxes", species)
            return 1
        while len(party(d)) >= 6:
            v = next((i for i, m in enumerate(party(d))
                      if spn(d, m) not in (LEAD, target)), None)
            if v is None or not st.deposit(v):
                break
        if not st.withdraw(hit[0] // 30, hit[0] % 30):
            log.info("withdraw %s refused: %s", species,
                     getattr(st, "last_reason", "?"))
            return 1

    # 3. THE LEAD MUST BE THE L100, NOT THE TARGET. Deposit anything that
    #    would stand in front of it; a benched holder is the whole point.
    for _ in range(8):
        p = party(d)
        if p and spn(d, p[0]) == LEAD:
            break
        drop = next((i for i, m in enumerate(p) if spn(d, m) != LEAD), None)
        if drop is None or len(p) <= 1:
            break
        if not st.deposit(drop):
            log.info("deposit refused: %s", getattr(st, "last_reason", "?"))
            break

    # 4. bring the target back in behind the lead
    fresh = DexTarget(d.emu, d.names, d.consts, d.nav, spec=d.spec)
    if not any(spn(d, m) == target for m in party(d)):
        hit = next(((s, mo) for s, mo in fresh.boxed()
                    if d.names.species(mo.species).upper() == target), None)
        if hit is None or not st.withdraw(hit[0] // 30, hit[0] % 30):
            log.info("could not re-add %s", target)
            return 1

    # REDUCE TO EXACTLY [LEAD, TARGET]. The only give that has ever landed
    # correctly in this project put the target at SLOT 1 in a two-mon party
    # (MARILL, which then evolved into AZUMARILL). With a third mon present
    # the same call put the share on slot 0 -- the level-100, which earns
    # nothing -- and four Elite Four laps paid nobody. The party picker's
    # geometry is still unsolved, so instead of fighting it, remove the
    # ambiguity: two mons, target second.
    for _ in range(8):
        p2 = party(d)
        if len(p2) <= 2:
            break
        drop = next((i for i, m in enumerate(p2)
                     if spn(d, m) not in (LEAD, target)), None)
        if drop is None or not st.deposit(drop):
            break

    from pokeagent.teaching import ITEMS_POCKET
    log.info("pocket0 ids before give: %s",
             [r[1] for r in t.pocket_items(ITEMS_POCKET)])
    log.info("held_items: %s", [(spn(d, m), m.held_item) for m in party(d)])

    # 5. hand the share over and prove who holds it
    idx = next((i for i, m in enumerate(d.state.party())
                if spn(d, m) == target), None)
    if idx is None:
        log.info("%s not in the party after staging", target)
        return 1
    # GIVE FROM THE FIELD MENU, NOT THE BAG. `give_to_mon` picks its slot
    # inside the bag's give flow, whose party cursor this project has never
    # read correctly: the share aimed at LOUDRED landed on the level-100
    # PELIPPER, which earns nothing, and four laps paid nobody.
    if not (t.give_to_mon(SHARE_NAME, d.state.party()[idx].nickname)
            or t.give_from_field(idx, SHARE_NAME)):
        log.info("could not give the share: %s", getattr(t, "last_reason", "?"))
        return 1
    h = holder(d)
    if h is None or spn(d, d.state.party()[h]) != target:
        log.info("share is on %s, not %s -- refusing to run a lap that pays "
                 "nobody", spn(d, d.state.party()[h]) if h is not None
                 else "nobody", target)
        return 1
    log.info("share landed on slot %d (%s)", h, spn(d, d.state.party()[h]))

    # ESCORT THE HOLDER, but only AFTER the give has landed -- the give is
    # only reliable with exactly two mons in the party. With no third mon,
    # PELIPPER fainting forces the holder into the ring, where it is knocked
    # out and stops earning for the rest of the lap.
    fresh2 = DexTarget(d.emu, d.names, d.consts, d.nav, spec=d.spec)
    # FILL ALL SIX SLOTS. The holder can only be dragged into the ring as a
    # FORCED replacement, which happens once every escort in front of it has
    # fainted -- and a fainted mon earns nothing for the rest of the run
    # (`battle_script_commands.c:3361-3364`). With only two escorts the holder
    # was being knocked out partway through the first pass (`A 0/44`), so the
    # second and third passes of a lap paid it nothing at all. Four escorts is
    # four more faints the gauntlet has to get through first.
    for species in ("BLAZIKEN", "AGGRON", "MIGHTYENA", "NINJASK",
                    "WHISCASH", "LOMBRE", "SWELLOW", "LINOONE"):
        if len(party(d)) >= 6:
            break
        if any(spn(d, m) == species for m in party(d)):
            continue
        hit = next(((sl, mo) for sl, mo in fresh2.boxed()
                    if d.names.species(mo.species).upper() == species), None)
        if hit is None:
            continue
        if st.withdraw(hit[0] // 30, hit[0] % 30):
            log.info("escort %s added", species)

    try:
        d.heal_at_nearest_center()
    except Exception:  # noqa: BLE001
        pass
    if holder(d) is None or spn(d, d.state.party()[holder(d)]) != target:
        log.info("the share moved off %s while adding escorts -- aborting",
                 target)
        return 1
    log.info("party %s", [(spn(d, m), m.level, m.held_item) for m in party(d)])

    # 6. park in the League hall for elite_four.py
    try:
        for _ in range(4):
            if d.flight.flyable_here():
                break
            d.flight.step_outside()
        d.fly_to("EverGrandeCity")
        import league_loop
        league_loop.into_hall(d)
    except Exception as e:  # noqa: BLE001
        log.info("into_hall: %s", str(e)[:100])
    log.info("parked at %s %s", d.map_name(), d.pos())
    d.save(a.state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
