#!/usr/bin/env python
"""Level ONE named mon on the open sea until it can carry the Wallace fight.

Badge 8 was attacked three ways and each returned the battle module's own
`stalled` -- four consecutive turns in which neither HP bar moved. Best typing
(TM34 Shock Wave), overwhelming items (31 Hyper Potions) and stat multiplication
(X Attack to the +6 cap) all failed against a L48 Milotic that simply out-heals
an L38-48 roster. The blocker is damage, and damage here is levels.

Wild XP is the only source left: Victory Road and the Elite Four sit behind the
badge, and the gym's own trainers are already spent. Wild XP goes to whoever is
in front, so this grinds ONE mon deliberately rather than spreading it thin --
PELIPPER carrying Shock Wave, which is 2x on every member of Wallace's roster
and the only super-effective move the team has ever had.

Sootopolis' lake is Magikarp and Tentacool and moved nobody a level in fifty
battles; the open sea north of Mossdeep (Routes 126-128, Wingull/Pelipper/
Tentacruel/Sharpedo in the 30s) is several times better and, unlike Sky Pillar,
is water this run can actually reach -- see PROGRESS.md port-38 for why the
pillar's lagoon is not connected to any sea we can stand in.
"""

from __future__ import annotations

import argparse
import logging
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pokeagent.trek import Driver  # noqa: E402

log = logging.getLogger("sea")

#: Open water with mid-30s encounters, all reachable from Mossdeep on foot.
SEAS = ["Route127", "Route126", "Route128"]

#: Fly landing with a Pokemon Center, for the heal trips.
HOME = "MossdeepCity"


def slot_of(d, nick: str) -> int | None:
    for i, m in enumerate(d.state.party()):
        if m.nickname == nick:
            return i
    return None


def make_policy(d, nick: str):
    """Switch the target in, then hit. Wild XP goes to whoever PARTICIPATES,
    so switching on turn one levels it without touching the overworld party
    menu -- `PartyOrder.lead_with` returns False on this save (a known harness
    gap) and blind A-loops through that screen are what cost an earlier run
    five party members."""
    switched = {"done": False}

    def policy(frame):
        me = (frame or {}).get("me") or {}
        active = me.get("nickname") or me.get("name")
        idx = slot_of(d, nick)
        if idx is None:
            return None
        if active != nick and not switched["done"]:
            switched["done"] = True
            mon = d.state.party()[idx]
            if mon.hp > 0:
                return ("switch", idx)
        return None          # fall through to the harness' own best move

    return policy


def heal(d) -> bool:
    """Fly home and use the Center. The door comes from the map's warp table;
    walking whatever `exits()` lists first wandered into the wrong buildings."""
    if not d.fly_to(HOME):
        return False
    if "PokemonCenter" not in d.map_name():
        for w in (d.nav.info(HOME).warps or []):
            if "POKEMON_CENTER" not in str(w.dest_map):
                continue
            try:
                d.reach_cell(w.x, w.y + 1, map_name=HOME, on_battle="fight")
            except Exception:  # noqa: BLE001
                pass
            if d.take_warp(w.x, w.y):
                break
    ok = d.heal()
    for e in d.exits():
        if e.get("kind") == "warp":
            d.take_warp(e["x"], e["y"])
            break
    return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True)
    ap.add_argument("--out")
    ap.add_argument("--mon", default="SEA BIRD")
    ap.add_argument("--target", type=int, default=55)
    ap.add_argument("--minutes", type=float, default=600.0)
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    out = a.out or a.state
    d = Driver(a.state)
    rng = random.Random(7)
    deadline = time.time() + a.minutes * 60
    battles = 0
    start = [m.level for m in d.state.party()]
    log.info("START %s %s mon=%s levels %s", d.map_name(), d.pos(), a.mon,
             start)

    while time.time() < deadline:
        try:
            if d.in_battle():
                # FRESH POLICY PER BATTLE. The switch flag lives in the
                # closure, so a single shared policy switched the target in
                # once and never again -- ten minutes of fighting left
                # PELIPPER at exactly the level it started.
                d.fight(policy=make_policy(d, a.mon))
                battles += 1
                d.save(out)
                continue

            idx = slot_of(d, a.mon)
            if idx is None:
                log.info("no mon named %r in the party", a.mon)
                return 1

            lead = d.state.party()[idx]
            if lead.level >= a.target:
                log.info("DONE %s reached L%d after %d battles", a.mon,
                         lead.level, battles)
                d.save(out)
                return 0
            if lead.hp * 3 < lead.max_hp:
                log.info("healing (%s at %d/%d)", a.mon, lead.hp, lead.max_hp)
                heal(d)
                d.save(out)

            if d.map_name() not in SEAS:
                # FLY HOME FIRST. Routes 129/130/131 south are a closed sea
                # (PROGRESS.md port-38) from which none of SEAS is reachable,
                # so travel() there just fails on its first move forever.
                if d.map_name() not in (HOME,):
                    d.fly_to(HOME)
                for sea in SEAS:
                    try:
                        if d.travel(sea, on_battle="fight"):
                            break
                    except Exception:  # noqa: BLE001
                        if d.in_battle():
                            break
                d.save(out)
                continue

            # Wander the water. Encounters fire on steps, so this walks until
            # one triggers and lets the top of the loop play it.
            moved = 0
            for _ in range(40):
                if d.in_battle():
                    break
                if d.step_dir(rng.choice("UDLR")):
                    moved += 1
            if not d.in_battle() and moved == 0:
                # Boxed in -- try another sea.
                d.travel(rng.choice(SEAS), on_battle="fight")
        except Exception as exc:  # noqa: BLE001
            log.info("recovering from %s: %s", type(exc).__name__,
                     str(exc)[:90])
            try:
                d.close_menus()
                if d.in_battle():
                    d.fight(policy=Driver.damage_first)
            except Exception:  # noqa: BLE001
                pass
            d.save(out)

    log.info("STOP after %d battles; levels %s -> %s", battles, start,
             [m.level for m in d.state.party()])
    d.save(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
