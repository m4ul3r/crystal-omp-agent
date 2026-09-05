#!/usr/bin/env python
"""Level boxed Pokemon just far enough to evolve, and register the evolution.

22 of the missing species are evolutions of mons already owned, and four of
them are ALREADY past their threshold -- they evolve on their very next
level-up:

    BARBOACH L35 -> WHISCASH   (needs 30)
    LOUDRED  L40 -> EXPLOUD    (needs 40)
    MARILL   L25 -> AZUMARILL  (needs 18)
    NATU     L25 -> XATU       (needs 25)

Boxed levels are derived from EXP (`Names.level_from_exp`), because the box
format has no level field at all.

The shape: bring a target into the party with `Storage`, put it in FRONT so it
is guaranteed to participate (XP in Gen 3 goes to participants, and a
level-100 lead would otherwise take every kill), then pace grass until its
level moves. Success is judged by the DEX FLAG, not by the level or by the
script finishing -- an evolution that does not register is not a dex entry.

Every target is banked as soon as it lands, so a crash costs one target and
not the run.
"""

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pokeagent.trek import Driver, TravelInterrupted  # noqa: E402
from pokeagent.dex import DexTarget  # noqa: E402
from pokeagent.storage import Storage  # noqa: E402
from pokeagent.partyorder import PartyOrder  # noqa: E402

log = logging.getLogger("evolve")

#: Party slots that must never be deposited: the lead is the only thing that
#: can finish a fight the target cannot.
KEEP = {"SEA BIRD"}


def caught(t, state):
    return len(t.dex_flags(state)[0])


def find_boxed(t, d, species):
    """``(flat_slot, level)`` of the highest-level boxed mon of a species."""
    best = None
    for slot, mon in t.boxed():
        if d.names.species(mon.species) != species:
            continue
        lv = t.boxed_level(mon)
        if best is None or lv > best[1]:
            best = (slot, lv)
    return best


def make_room(s, d) -> bool:
    """Deposit one expendable party member. Never the lead."""
    party = s.party_names()
    if len(party) < 6:
        return True
    for i in range(len(party) - 1, 0, -1):
        if party[i] in KEEP:
            continue
        return s.deposit(i)
    return False


def xp_share(d, target, finisher="SEA BIRD"):
    """A per-battle policy: register the target, then let the finisher work.

    Gen 3 splits experience among every mon that was SENT OUT, so the target
    only has to APPEAR -- it does not have to land the kill, and it does not
    have to LEAD. Switching it in on turn one and back out on turn two earns
    it a share for the price of at most one hit.

    Not leading is deliberate: `PartyOrder.lead_with` could not open the
    party popup here ("row 0 left the party screen and B did not return"), and
    a reorder is not needed for XP anyway.

    That matters because tactics protects a mon it judges to be losing by
    FLEEING, and a fled battle pays nothing. Measured on the first attempt:
    MARILL led, dropped 106 -> 84 -> 65 -> 29 HP, and then chose `flee RUN`
    on ten consecutive encounters while earning zero experience.
    """
    step = {"n": 0}

    def index_of(nick, alive=True):
        """Party index, skipping a FAINTED mon.

        The engine refuses a switch to a fainted slot, and the policy kept
        nominating one anyway: "a switch was refused (NATU (slot 5) has
        fainted)" on every encounter, after which tactics fled and the run
        earned nothing for ten minutes.
        """
        for i, m in enumerate(d.state.party()):
            if m.nickname != nick:
                continue
            if alive and not (m.hp or 0):
                return None
            return i
        return None

    def policy(frame):
        me = (frame or {}).get("me") or {}
        active = me.get("nickname") or me.get("name")
        step["n"] += 1
        # Turn 1: put the target on the field. That is the whole requirement
        # -- being SENT OUT is what makes it a participant.
        if step["n"] == 1 and active != target:
            idx = index_of(target)
            if idx is not None:
                return ("switch", idx)
        # AND IT STAYS IN. Switching back was the plan and the engine
        # refuses it -- "confirmed SHIFT to slot 0 but gBattlerPartyIndexes
        # is [5]" every time -- so the target was stranded on the field at
        # low level and fainted to the incoming hit instead.
        #
        # Leaving it in is fine PROVIDED the grinding ground is weak enough
        # for it to win on its own, which is why this pairs with a low-level
        # route (Route117 is L13-14) rather than Route119's L25-30.
        return None            # tactics plays whoever is out

    return policy


def reach_grass(d, grass, fly_town) -> bool:
    """Fly, then walk. `travel` alone never leaves the landmass it is on."""
    if d.map_name() == grass:
        return True
    if fly_town:
        try:
            d.fly_to(fly_town)
            d.advance_scene(20_000)
        except Exception as exc:  # noqa: BLE001
            log.info("   fly %s: %s", fly_town, str(exc)[:90])
    try:
        d.travel(grass, on_battle="fight", budget_s=240)
    except Exception as exc:  # noqa: BLE001
        log.info("   travel %s: %s", grass, str(exc)[:100])
    return d.map_name() == grass


def grind_one(d, t, target, deadline, per_target) -> bool:
    """Pace grass with `target` participating until its evolution registers.

    Always clears `_journey_deadline` on the way out. Leaving it set poisoned
    every LATER navigation in the run: the next three targets each failed with
    "journey budget spent at (7,8) heading for (10,2)" while walking four
    tiles to a PC, because they inherited a deadline that expired during the
    grind.
    """
    before = caught(t, d.state)
    import time
    stop = min(deadline, time.time() + per_target)
    here = d.map_name()
    try:
        grass = [g for g in d.nav.find_tiles(here, "grass")
                 if g in set(d.nav.reachable(here, d.pos(), d.elevation()))]
    except Exception:  # noqa: BLE001
        grass = []
    if not grass:
        log.info("   no reachable grass on %s", here)
        return False
    log.info("   %d grass cells; grinding %s", len(grass), target)

    def target_down():
        """Is the target actually fainted?

        Only trustworthy OUTSIDE a scene. gPlayerParty's HP is written back
        when a battle ends, so reading it mid-battle reported 0 for a mon
        that was at full health -- the run then "healed" a NATU sitting at
        62/62 twice in a row and gave up on it.
        """
        if d.scene_active():
            return False
        m = next((m for m in d.state.party() if m.nickname == target), None)
        if m is None:
            return True
        return (m.max_hp or 0) > 0 and not (m.hp or 0)

    i = 0
    heals = 0
    while time.time() < stop:
        i += 1
        # A FAINTED TARGET EARNS NOTHING. It cannot be switched in, so the
        # whole point of the run is gone until it is healed. The Centre is
        # free and adjacent; potions are not (the bag has none).
        if target_down():
            if heals >= 6:
                log.info("   %s keeps fainting; giving up on it", target)
                return False
            heals += 1
            log.info("   %s is down -- healing (%d)", target, heals)
            try:
                d.heal_at_nearest_center()
                d.advance_scene(40_000)
                if d.map_name() != here:
                    d.travel(here, on_battle="fight", budget_s=180)
            except Exception as exc:  # noqa: BLE001
                log.info("   heal: %s", str(exc)[:90])
            if target_down():
                log.info("   still down after healing; stopping")
                return False
            continue
        cell = grass[(i * 7) % len(grass)]
        try:
            d._journey_deadline = min(stop, time.time() + 45.0)
            d.goto(*cell, on_battle="raise")
        except TravelInterrupted:
            try:
                # A FRESH policy per battle: the swap flag has to reset, or
                # only the first encounter of the run would register.
                d.fight(policy=xp_share(d, target))
            except Exception as exc:  # noqa: BLE001
                log.debug("fight: %s", str(exc)[:70])
            d.advance_scene(30_000)
            now = caught(t, d.state)
            if now > before:
                names = [m.nickname for m in d.state.party()]
                log.info("   EVOLVED: dex %d -> %d (party %s)",
                         before, now, names)
                d._journey_deadline = None
                return True
        except Exception as exc:  # noqa: BLE001
            log.debug("pace: %s", str(exc)[:70])
        finally:
            d._journey_deadline = None
    d._journey_deadline = None
    log.info("   %s did not evolve in its budget", target)
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True)
    ap.add_argument("--centre", default="FortreeCity_PokemonCenter_1F")
    ap.add_argument("--grass", default="Route119")
    ap.add_argument("--fly", default=None,
                    help="town to FLY to before walking to --grass. travel() "
                         "does not fly, so a route on the far side of Hoenn "
                         "is simply unreachable without this -- three targets "
                         "were skipped in a row learning that")
    ap.add_argument("--targets", default="MARILL,NATU,BARBOACH,LOUDRED")
    ap.add_argument("--minutes", type=float, default=240.0)
    ap.add_argument("--per-target", type=float, default=1800.0)
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    import time
    deadline = time.time() + a.minutes * 60.0

    d = Driver(a.state)
    d.advance_scene(40_000)
    d.nav.surfing = True
    t = DexTarget(d.emu, d.names, d.consts, d.nav, spec=d.spec)
    s = Storage(d)
    log.info("start %s %s | %s", d.map_name(), d.pos(), t.summary(d.state))

    for species in [x.strip() for x in a.targets.split(",") if x.strip()]:
        if time.time() > deadline:
            break
        log.info("=== %s ===", species)
        # ALREADY IN THE PARTY? Then there is nothing to withdraw, and going
        # to a Centre to do it is how three targets got skipped in a row --
        # the run was pointed at Mauville's Centre while standing in
        # Fortree's, could not travel there, and abandoned each one.
        in_party = next((m.nickname for m in d.state.party()
                         if d.names.species(m.species) == species), None)
        if in_party is not None:
            log.info("   %s is already in the party as %s", species, in_party)
            if reach_grass(d, a.grass, a.fly):
                if grind_one(d, t, in_party, deadline, a.per_target):
                    d.save(a.state)
            else:
                log.info("   could not reach %s", a.grass)
            log.info("   banked | %s", t.summary(d.state))
            continue
        hit = find_boxed(t, d, species)
        if hit is None:
            log.info("   no boxed %s", species)
            continue
        slot, lv = hit
        log.info("   boxed %s L%d at box %d pos %d", species, lv,
                 slot // 30, slot % 30)
        try:
            if d.map_name() != a.centre:
                d.travel(a.centre, on_battle="fight", budget_s=240)
        except Exception as exc:  # noqa: BLE001 - a wall must not end the run
            log.info("   travel to %s: %s", a.centre, str(exc)[:100])
        if d.map_name() != a.centre:
            log.info("   could not reach %s", a.centre)
            continue
        if not make_room(s, d):
            log.info("   could not free a party slot: %s", s.last_reason)
            continue
        if not s.withdraw(slot // 30, slot % 30):
            log.info("   withdraw failed: %s", s.last_reason)
            continue
        nick = [n for n in s.party_names()][-1]
        # HEAL BEFORE LEAVING. A boxed mon keeps the HP it was deposited
        # with -- the withdrawn NATU came out at 3/62, so switching it in
        # fainted it on the incoming hit and the run learned nothing except
        # how to flee. The nurse is in this same room and costs nothing.
        try:
            d.heal()
            d.advance_scene(40_000)
        except Exception as exc:  # noqa: BLE001
            log.info("   heal after withdraw: %s", str(exc)[:90])
        hp = next((m.hp for m in d.state.party() if m.nickname == nick), None)
        log.info("   %s is at %s HP after healing", nick, hp)
        d.save(a.state)
        if not reach_grass(d, a.grass, a.fly):
            log.info("   could not reach %s", a.grass)
            continue
        if grind_one(d, t, nick, deadline, a.per_target):
            d.save(a.state)
        log.info("   banked | %s", t.summary(d.state))

    log.info("done | %s", t.summary(d.state))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
