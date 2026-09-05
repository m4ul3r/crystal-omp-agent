#!/usr/bin/env python
"""Spend an evolution stone on a boxed Pokemon, for the dex entry.

Now possible because box paging works: nearly every stone target lives in
boxes 1-2, which were unreachable until the D-pad-from-the-title fix
(pokemon_storage_system_4.c:2107-2109).

Sapphire sells NO stones -- Lilycove 5F is a decoration list -- so each one
is a single ground item and every use is one-shot. Pairs handled here, all
from the ROM's own table (pret/src/data/pokemon/evolution.h):

    MOON STONE  + JIGGLYPUFF -> WIGGLYTUFF
    MOON STONE  + SKITTY     -> DELCATTY
    SUN  STONE  + GLOOM      -> BELLOSSOM
    WATER STONE + LOMBRE     -> LUDICOLO
    WATER STONE + STARYU     -> STARMIE
    FIRE  STONE + VULPIX     -> NINETALES
    THUNDER STONE + PIKACHU  -> RAICHU
"""
import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pokeagent.trek import Driver  # noqa: E402
from pokeagent.dex import DexTarget  # noqa: E402
from pokeagent.storage import Storage  # noqa: E402
from pokeagent.teaching import Teacher  # noqa: E402

log = logging.getLogger("stone_evolve")

BOX_SIZE = 30

PAIRS = {
    "JIGGLYPUFF": ("MOON STONE", "Wigglytuff"),
    "SKITTY": ("MOON STONE", "Delcatty"),
    "GLOOM": ("SUN STONE", "Bellossom"),
    "LOMBRE": ("WATER STONE", "Ludicolo"),
    "STARYU": ("WATER STONE", "Starmie"),
    "VULPIX": ("FIRE STONE", "Ninetales"),
    "PIKACHU": ("THUNDER STONE", "Raichu"),
}


def held(d, item: str) -> bool:
    want = item.upper()
    try:
        for pocket in d.state.bag().values():
            if isinstance(pocket, dict) and any(
                    str(k).upper() == want for k in pocket):
                return True
    except Exception:  # noqa: BLE001
        pass
    return False


def dex_count(dex, state) -> int:
    import re

    m = re.search(r"dex (\d+)/", dex.summary(state))
    return int(m.group(1)) if m else -1


def in_party(d, species: str):
    return next((m for m in d.state.party()
                 if not m.is_egg
                 and d.names.species(m.species).upper() == species.upper()),
                None)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True)
    ap.add_argument("--species", required=True,
                    help="the PRE-evolution to withdraw and evolve")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    species = a.species.upper()
    if species not in PAIRS:
        log.info("no stone pairing known for %s", species)
        return 1
    stone, becomes = PAIRS[species]

    d = Driver(a.state)
    d.advance_scene(40_000)
    dex = DexTarget(d.emu, d.names, d.consts, d.nav, spec=d.spec)
    before = dex_count(dex, d.state)
    log.info("start %s | dex %d | %s held = %s", d.map_name(), before,
             stone, held(d, stone))

    if not held(d, stone):
        log.info("no %s in the bag -- nothing to spend", stone)
        return 1

    mon = in_party(d, species)
    if mon is None:
        # Find it in the boxes and bring it out.
        found = None
        for slot, boxed in dex.boxed():
            try:
                if d.names.species(boxed.species).upper() == species:
                    found = (slot, dex.boxed_level(boxed))
                    break
            except Exception:  # noqa: BLE001
                continue
        if found is None:
            log.info("no boxed %s either", species)
            return 1
        slot, lv = found
        log.info("boxed %s L%s at flat slot %d (box %d slot %d)",
                 species, lv, slot, slot // BOX_SIZE, slot % BOX_SIZE)
        if not Storage(d).pc_cells():
            try:
                d.heal_at_nearest_center()
            except Exception as exc:  # noqa: BLE001
                log.info("centre: %s", str(exc)[:90])
        st = Storage(d)
        if not st.pc_cells():
            log.info("no PC on %s", d.map_name())
            return 1
        if len(d.state.party()) >= 6:
            # Never bank the egg: it is a pending dex entry mid-hatch.
            cands = [(i, m) for i, m in enumerate(d.state.party())
                     if not m.is_egg]
            i, victim = min(cands, key=lambda p: p[1].level or 0)
            log.info("depositing %s L%s to make room", victim.nickname,
                     victim.level)
            if not st.deposit(i):
                log.info("deposit refused: %s",
                         getattr(st, "last_reason", "?"))
                return 1
        if not st.withdraw(slot // BOX_SIZE, slot % BOX_SIZE):
            log.info("withdraw refused: %s", getattr(st, "last_reason", "?"))
            return 1
        st.close()
        mon = in_party(d, species)
        if mon is None:
            log.info("%s is not in the party after withdraw", species)
            return 1

    nick = mon.nickname or species
    log.info("using %s on %s L%s", stone, nick, mon.level)
    t = Teacher(d)
    ok = t.use_on_mon(stone, nick)
    log.info("use_on_mon -> %s (%s)", ok, getattr(t, "last_reason", None))
    d.advance_scene(60_000)

    dex = DexTarget(d.emu, d.names, d.consts, d.nav, spec=d.spec)
    now = dex_count(dex, d.state)
    log.info("party now: %s",
             [(d.names.species(m.species), m.level)
              for m in d.state.party() if not m.is_egg])
    log.info("dex %d -> %d | %s", before, now,
             dex.summary(d.state).split(";")[0])
    d.save(a.state)
    if now > before:
        log.info("*** %s REGISTERED ***", becomes.upper())
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
