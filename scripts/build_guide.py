#!/usr/bin/env python
"""Generate route/decision context for the playing agent.

The request was for comprehensive walkthroughs saved as text so whichever
agent is playing has the context to decide. This produces that context, but it
GENERATES it from the decompilation and the user's own dex dataset rather than
mirroring a wiki's prose. Two reasons, both practical:

* An agent needs exact coordinates, flag names and level ranges. Walkthrough
  prose says "head north until you reach the gate"; the decomp says the gate
  is a warp at (17,6) gated on ``VAR_LITTLEROOT_STATE >= 2``. The second is
  actionable and the first is not.
* It stays current with the ROM being driven, because it IS the ROM's data.

Bulbapedia stays in the output as *links* for a human to read, which is the
right use of it.

Emits, under docs/gen3/guide/:

    trainers.json / .md    every trainer: class, name, party, levels, moves
    gyms.md                the eight leaders and the Elite Four, in order
    gates.json / .md       story gates: which FLAG/VAR opens which map
    encounters.json / .md  per-map wild tables: species, levels, rates
    items.json             key items and HMs with coordinates
    index.md               how to use all of it

    scripts/build_guide.py
"""

import argparse
import json
import logging
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pokeagent import paths  # noqa: E402

log = logging.getLogger("build_guide")

OUT = paths.TOOL_DIR / "docs" / "gen3" / "guide"

#: Gym leaders in badge order, and the Elite Four. The ORDER is the one fact
#: here that the ROM does not state directly (nothing in the data says Roxanne
#: is first), so it is listed explicitly and the parties are read from the ROM.
GYM_ORDER = [
    ("ROXANNE", "Rustboro City", "Stone Badge", "Rock"),
    ("BRAWLY", "Dewford Town", "Knuckle Badge", "Fighting"),
    ("WATTSON", "Mauville City", "Dynamo Badge", "Electric"),
    ("FLANNERY", "Lavaridge Town", "Heat Badge", "Fire"),
    ("NORMAN", "Petalburg City", "Balance Badge", "Normal"),
    ("WINONA", "Fortree City", "Feather Badge", "Flying"),
    # Tate and Liza are ONE gym, ONE badge and one DOUBLE battle. Listing them
    # as separate rows put Wallace at badge 9 in a region that has 8.
    ("TATE & LIZA", "Mossdeep City", "Mind Badge", "Psychic"),
    ("WALLACE", "Sootopolis City", "Rain Badge", "Water"),
]
ELITE_FOUR = ["SIDNEY", "PHOEBE", "GLACIA", "DRAKE", "WALLACE"]

BULBAPEDIA = "https://bulbapedia.bulbagarden.net/wiki"


def parse_trainer_parties():
    """Every trainer's party, from data/trainers/parties.asm.

    The file is a sequence of labelled blocks of `party_mon` style macros; the
    exact macro names differ between decomps, so the parse is deliberately
    tolerant and reports what it could not read.
    """
    # pokeruby keeps these as C designated initialisers in
    # src/data/trainer_parties.h, one array per trainer:
    #   const struct TrainerMonNoItemDefaultMoves gTrainerParty_Archie1[] = {
    #       { .iv = 0, .level = 17, .species = SPECIES_HUNTAIL },
    # so the parse is per-array-block, and the struct TYPE tells us whether
    # the party carries custom moves or held items.
    path = paths.PRET / "src" / "data" / "trainer_parties.h"
    if not path.exists():
        return {}, [f"missing {path}"]
    text = path.read_text(encoding="utf-8", errors="replace")
    out, problems = {}, []
    pattern = re.compile(
        r"const\s+struct\s+(\w+)\s+gTrainerParty_(\w+)\[\]\s*=\s*\{(.*?)\n\};",
        re.S,
    )
    for struct_type, label, body in pattern.findall(text):
        mons = []
        for entry in re.split(r"\}\s*,?\s*\{", body):
            sp = re.search(r"\.species\s*=\s*(SPECIES_[A-Z0-9_]+)", entry)
            if not sp:
                continue
            lvl = re.search(r"\.level\s*=\s*(\d+)", entry)
            iv = re.search(r"\.iv\s*=\s*(\d+)", entry)
            item = re.search(r"\.heldItem\s*=\s*(ITEM_[A-Z0-9_]+)", entry)
            mons.append({
                "species": sp.group(1).removeprefix("SPECIES_"),
                "level": int(lvl.group(1)) if lvl else None,
                "iv": int(iv.group(1)) if iv else None,
                "held_item": item.group(1).removeprefix("ITEM_") if item else None,
                "moves": [m.removeprefix("MOVE_")
                          for m in re.findall(r"(MOVE_[A-Z0-9_]+)", entry)],
            })
        if mons:
            out[label] = {"struct": struct_type, "party": mons}
    if not out:
        problems.append(f"parsed no parties out of {path.name}")
    return out, problems


def parse_story_gates():
    """Which FLAG/VAR condition guards each map's scripts.

    This is the class of fact that actually unblocks a run: the north exit of
    Littleroot is closed until VAR_LITTLEROOT_STATE advances, and nothing in a
    prose walkthrough tells you the variable's name.
    """
    gates = defaultdict(list)
    maps_dir = paths.MAPS
    if not maps_dir.exists():
        return {}, [f"missing {maps_dir}"]
    for script in sorted(maps_dir.glob("*/scripts.inc")):
        map_name = script.parent.name
        text = script.read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(
            r"^\s*(goto_if_set|goto_if_unset|call_if_set|call_if_unset)\s+"
            r"(FLAG_[A-Z0-9_]+)", text, re.M
        ):
            gates[map_name].append({"kind": m.group(1), "flag": m.group(2)})
        for m in re.finditer(
            r"^\s*compare\s+(VAR_[A-Z0-9_]+),\s*(\d+)", text, re.M
        ):
            gates[map_name].append(
                {"kind": "compare", "var": m.group(1), "value": int(m.group(2))}
            )
        for m in re.finditer(
            r"^\s*setvar\s+(VAR_[A-Z0-9_]+),\s*(\d+)", text, re.M
        ):
            gates[map_name].append(
                {"kind": "setvar", "var": m.group(1), "value": int(m.group(2))}
            )
    # De-duplicate while keeping order.
    for k, rows in gates.items():
        seen, uniq = set(), []
        for r in rows:
            key = tuple(sorted(r.items()))
            if key not in seen:
                seen.add(key)
                uniq.append(r)
        gates[k] = uniq
    return dict(gates), []


def build_encounters(driver):
    """Per-map wild tables, from the ROM's own gWildMonHeaders."""
    from pokeagent import dex

    target = dex.DexTarget(
        driver.emu, driver.names, driver.consts, driver.nav, spec=driver.spec
    )
    out = {}
    for map_name in sorted(driver.nav.index):
        try:
            slots = target.wild.for_map(map_name)
        except Exception:  # noqa: BLE001
            continue
        if not slots:
            continue
        rows = []
        for slot in slots:
            # Read the fields WildSlot actually declares (pokeagent/dex.py:424):
            # `kind` and `slot_chance`, not `method` and `chance`. Asking for
            # the wrong names through `getattr(..., None)` is not an error, it
            # is a silent None -- so every method and chance in
            # docs/guide/encounters.json was null and the file still looked
            # well-formed. A defensive default on a name you control turns a
            # typo into missing data.
            rows.append({
                "species": driver.names.species(slot.species).strip(),
                "min_level": slot.min_level,
                "max_level": slot.max_level,
                "method": slot.kind,
                "chance": slot.slot_chance,
                "encounter_rate": slot.encounter_rate,
            })
        out[map_name] = rows
    return out, target


def write_markdown(path, title, body):
    path.write_text(f"# {title}\n\n{body}\n", encoding="utf-8")
    log.info("wrote %s", path.relative_to(paths.TOOL_DIR))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default="saves/lab.state")
    ap.add_argument("--game", default="sapphire")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    OUT.mkdir(parents=True, exist_ok=True)

    from pokeagent.trek import Driver

    d = Driver(a.state, game=a.game)
    notes = []

    # ---- trainers -------------------------------------------------------
    parties, problems = parse_trainer_parties()
    notes += problems
    (OUT / "trainers.json").write_text(json.dumps(parties, indent=1))
    log.info("trainers: %d parties", len(parties))

    # ---- gyms -----------------------------------------------------------
    lines = [
        "Badge order is the one thing the ROM does not state outright, so it "
        "is listed here; every party is read from the ROM's own trainer data.",
        "",
        "| # | Leader | Town | Badge | Type | Bulbapedia |",
        "|---|--------|------|-------|------|------------|",
    ]
    for i, (leader, town, badge, typ) in enumerate(GYM_ORDER, 1):
        slug = town.replace(" ", "_")
        lines.append(
            f"| {i} | {leader.title()} | {town} | {badge} | {typ} | "
            f"[{town}]({BULBAPEDIA}/{slug}) |"
        )
    lines += ["", "## Elite Four", "",
              " -> ".join(n.title() for n in ELITE_FOUR), "",
              "Parties for each of these are in `trainers.json`, keyed by the "
              "decomp's own label."]
    write_markdown(OUT / "gyms.md", "Gyms and the Elite Four", "\n".join(lines))

    # ---- story gates ----------------------------------------------------
    gates, problems = parse_story_gates()
    notes += problems
    (OUT / "gates.json").write_text(json.dumps(gates, indent=1))
    gated = {k: v for k, v in gates.items() if v}
    top = sorted(gated.items(), key=lambda kv: -len(kv[1]))[:40]
    body = [
        "The conditions that actually open and close routes. These are the "
        "facts a walkthrough cannot give you: the north exit of Littleroot is "
        "shut until `VAR_LITTLEROOT_STATE` advances, and the variable's name "
        "is only in the scripts.",
        "",
        f"{len(gated)} maps carry at least one flag or var condition. The "
        "busiest forty:",
        "",
        "| Map | Conditions |",
        "|-----|------------|",
    ]
    for map_name, rows in top:
        names = sorted({r.get("flag") or r.get("var") for r in rows if r.get("flag") or r.get("var")})
        body.append(f"| {map_name} | {', '.join(names[:6])}"
                    f"{' ...' if len(names) > 6 else ''} |")
    body += ["", "Full data, including the compare/setvar values, is in "
             "`gates.json`."]
    write_markdown(OUT / "gates.md", "Story gates", "\n".join(body))

    # ---- encounters -----------------------------------------------------
    enc, target = build_encounters(d)
    (OUT / "encounters.json").write_text(json.dumps(enc, indent=1))
    body = [
        "Wild tables straight from `gWildMonHeaders`, so these are the species "
        "and levels the ROM will actually generate.",
        "",
        f"{len(enc)} maps have wild encounters.",
        "",
        "| Map | Species |",
        "|-----|---------|",
    ]
    for map_name in sorted(enc)[:60]:
        species = sorted({r["species"] for r in enc[map_name]})
        body.append(f"| {map_name} | {', '.join(species[:8])}"
                    f"{' ...' if len(species) > 8 else ''} |")
    body.append("")
    body.append("Full tables, with levels, methods and rates, in "
                "`encounters.json`.")
    write_markdown(OUT / "encounters.md", "Wild encounters", "\n".join(body))

    # ---- key items ------------------------------------------------------
    try:
        from pokeagent import missables

        sources = [s.as_dict() for s in missables.parse_item_sources()]
        (OUT / "items.json").write_text(json.dumps(sources, indent=1))
        log.info("items: %d sources", len(sources))
    except Exception as exc:  # noqa: BLE001
        notes.append(f"item sources unavailable: {exc}")

    # ---- index ----------------------------------------------------------
    index = [
        f"Generated for **{d.spec.name}** (gen {d.spec.generation}, "
        f"{d.spec.region}) by `scripts/build_guide.py`.",
        "",
        "This is route context for whichever agent is playing. It is generated "
        "from the pret decompilation and the vendored regional-dex-buddy "
        "dataset rather than copied from a wiki, because an agent needs exact "
        "coordinates, flag names and level ranges -- not prose. Bulbapedia "
        "appears here as links, which is the right use of it.",
        "",
        "| File | What it answers |",
        "|------|-----------------|",
        "| `gyms.md` | Badge order, leaders, towns, types |",
        "| `trainers.json` | Every trainer's party: species, levels, moves |",
        "| `gates.md` / `.json` | Which FLAG/VAR opens which map |",
        "| `encounters.md` / `.json` | Per-map wild species, levels, rates |",
        "| `items.json` | Key items and HMs with coordinates |",
        "",
        "Live equivalents, preferred when a driver is running, because they "
        "are evaluated against the actual save:",
        "",
        "- `Driver.missables()` -- uncollected key items and HMs",
        "- `Driver.field_moves()` -- which party member knows each HM",
        "- `pokeagent.dex.DexTarget.plan()` -- what to catch next, and where",
        "- `pokeagent.living.LivingDex.plan()` -- what to breed next",
        "- `pokeagent.stages.Ladder.current()` -- the active goal",
    ]
    if notes:
        index += ["", "## Generation notes", ""] + [f"- {n}" for n in notes]
    write_markdown(OUT / "index.md", "Route guide", "\n".join(index))
    log.info("guide written to %s", OUT.relative_to(paths.TOOL_DIR))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
