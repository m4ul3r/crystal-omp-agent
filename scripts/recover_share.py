#!/usr/bin/env python
"""Get the EXP. SHARE back into the bag, then hand it to a grind target.

The share is the critical path for 38 of the 91 missing dex entries. A
BENCHED holder earns full battle experience without ever being sent out --
proven this run when GOLDEEN went L22 -> L28 off one Elite Four pass -- so a
single gauntlet can carry a L25 pre-evolution past its threshold and several
thresholds sit below L32.

It went missing because a mon was deposited still wearing it, which also
empties it from the bag. Reading `gPokemonStorage` directly (every box mon's
`held_item` is in the parsed struct) found it on NINJASK, box 2 slot 3 --
one read instead of 97 withdrawals.

NINJASK is L61, so recovering it also returns a real fighter to the party.
"""
import argparse, logging, sys, time
sys.path.insert(0, ".")

from pokeagent.trek import Driver, TravelError
from pokeagent.dex import DexTarget
from pokeagent.storage import Storage
from pokeagent.menus import Menus

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("share")

SHARE = 182


def party_holder(d):
    for i, m in enumerate(d.state.party()):
        if not m.is_egg and m.held_item == SHARE:
            return i, m
    return None, None


def boxed_share(d, dex):
    for slot, mon in dex.boxed():
        if mon.held_item == SHARE:
            return slot // 30, slot % 30, mon
    return None, None, None


def to_center(d):
    if d.map_name().endswith("PokemonCenter_1F"):
        return True
    try:
        d.heal_at_nearest_center()
    except Exception as e:  # noqa: BLE001
        log.info("heal_at_nearest_center: %s", str(e)[:90])
    if d.map_name().endswith("PokemonCenter_1F"):
        return True
    # Indoors refuses Fly, so step out first.
    for _ in range(4):
        if d.flight.flyable_here():
            break
        d.flight.step_outside()
    try:
        d.fly_to("MauvilleCity")
        d.heal_at_nearest_center()
    except Exception as e:  # noqa: BLE001
        log.info("fly/heal: %s", str(e)[:90])
    return d.map_name().endswith("PokemonCenter_1F")


def take_share(d, index):
    """Unequip the share from party slot `index` via the party screen.

    `Menus` is constructed here rather than reached through the Driver: this
    project has no `d.menu`, and assuming Crystal-era accessors has now cost
    four separate runs (`d.pace`, `d.deposit`, `d.map_objects`, `d.menu`).
    """
    m = Menus(d.emu, d.state)
    for attempt in range(6):
        d.close_menus(); d.settle(200)
        d.emu.run_sequence("START:6 .:60"); d.settle(400)
        if not (m.select_label("POK\xe9MON") or m.select_label("POKEMON")):
            continue
        d.settle(600)
        # The party screen is a two-column grid beside slot 0, so the picker
        # geometry is unreliable; try successive slots and judge by held_item.
        for _ in range(attempt):
            d.emu.run_sequence("DOWN:6 .:24")
        d.settle(300)
        d.emu.run_sequence("A:6 .:90"); d.settle(500)
        if m.select_label("ITEM"):
            d.settle(500)
            if m.select_label("TAKE"):
                d.settle(900)
                d.emu.run_sequence("A:6 .:120"); d.settle(600)
        d.close_menus(); d.settle(300)
        i, _ = party_holder(d)
        if i is None:
            return True
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True)
    a = ap.parse_args()

    d = Driver(a.state)
    d.advance_scene(40_000)
    dex = DexTarget(d.emu, d.names, d.consts, d.nav, spec=d.spec)
    log.info("start %s | %s", d.map_name(), dex.summary(d.state).split(";")[0])

    i, mon = party_holder(d)
    if i is not None:
        log.info("share already in the party on slot %d (%s)", i,
                 d.names.species(mon.species))
    else:
        box, slot, bmon = boxed_share(d, dex)
        if box is None:
            log.info("the share is in neither the party nor any box -- it is "
                     "in the bag or gone")
        else:
            log.info("share is on %s in box %d slot %d -- withdrawing",
                     d.names.species(bmon.species), box, slot)
            if not to_center(d):
                log.info("could not reach a Pokemon Center (at %s)", d.map_name())
                return 1
            st = Storage(d)
            party = [m for m in d.state.party() if not m.is_egg]
            if len(party) >= 6:
                # Free a slot with the lowest-level mon that is not the sweeper.
                victim = min(
                    ((n, m) for n, m in enumerate(party)
                     if (m.nickname or "").upper() != "SEA BIRD"),
                    key=lambda p: (p[1].level or 0))[0]
                log.info("party full -- depositing slot %d (%s L%s)", victim,
                         d.names.species(party[victim].species),
                         party[victim].level)
                st.deposit(victim)
            if not st.withdraw(box, slot):
                log.info("withdraw refused: %s", getattr(st, "last_reason", "?"))
                return 1
            d.save(a.state)
            i, mon = party_holder(d)
            log.info("withdrew -> share now on party slot %s", i)

    if i is None:
        log.info("no party holder; nothing to take")
    else:
        log.info("taking the share off %s", d.names.species(mon.species))
        if take_share(d, i):
            log.info("*** SHARE IS BACK IN THE BAG ***")
        else:
            j, m2 = party_holder(d)
            log.info("could not unequip (still on slot %s / %s)", j,
                     d.names.species(m2.species) if m2 else "?")
    d.save(a.state)
    log.info("party %s", [(d.names.species(m.species), m.level, m.held_item)
                          for m in d.state.party() if not m.is_egg])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
