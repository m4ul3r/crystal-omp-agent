#!/usr/bin/env python
"""Cold-read a fork: party, boxes, bag, dex -- for the CROBAT/SHEDINJA plan."""
import argparse, logging, sys
sys.path.insert(0, ".")
sys.path.insert(0, "scripts")

from pokeagent.trek import Driver
from pokeagent.dex import DexTarget

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("csi")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True)
    ap.add_argument("--maps", default="")
    a = ap.parse_args()

    d = Driver(a.state)
    d.advance_scene(40_000)
    t = DexTarget(d.emu, d.names, d.consts, d.nav, spec=d.spec)
    sp = lambda m: d.names.species(m.species).upper()

    log.info("map=%s pos=%s money=%s", d.map_name(), d.pos(), d.state.money())
    log.info("dex: %s", t.summary(d.state))
    caught, seen = t.dex_flags(d.state)
    log.info("caught=%d seen=%d", len(caught), len(seen))
    log.info("--- party (%d) ---", len(d.state.party()))
    for i, m in enumerate(d.state.party()):
        log.info("  %d %-10s %-10s L%-3s friend=%-3s item=%s exp=%s",
                 i, m.nickname, sp(m), m.level, m.friendship, m.held_item,
                 m.experience)
    log.info("--- boxed ---")
    for slot, m in t.boxed():
        log.info("  slot=%-3d box=%d/%-2d %-10s %-10s L%-3s friend=%-3s item=%s",
                 slot, slot // 30, slot % 30, m.nickname, sp(m),
                 t.boxed_level(m), m.friendship, m.held_item)
    log.info("free box slots=%d", t.box_free_slots())
    log.info("--- bag ---")
    for pocket, items in d.state.bag().items():
        log.info("  %s: %s", pocket, items)
    want = {"NINCADA", "GOLBAT", "ZUBAT", "CROBAT", "SHEDINJA", "NINJASK"}
    log.info("--- dex status for the line ---")
    for e in t.achievable:
        nm = getattr(e, "name", "").upper()
        if nm in want:
            log.info("  %-10s natdex=%-4s caught=%s seen=%s", nm, e.natdex,
                     e.natdex in caught, e.natdex in seen)
    for mp in [s for s in a.maps.split(",") if s]:
        try:
            rows = t.wild.for_map(mp)
        except Exception as exc:
            log.info("wild %s: %s", mp, exc)
            continue
        log.info("--- wild %s ---", mp)
        for r in rows:
            log.info("  %-10s %-9s L%s-%s chance=%s",
                     d.names.species(r.species).upper(), r.kind,
                     r.min_level, r.max_level, getattr(r, "slot_chance", "?"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
