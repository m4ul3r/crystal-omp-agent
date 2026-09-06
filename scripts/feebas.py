#!/usr/bin/env python
"""Catch FEEBAS on Route 119 by COMPUTING its six tiles from the save seed.

FEEBAS is the only wild species in the game whose location is a function of the
save file rather than of the map. Reading the cartridge instead of guessing:

* `CheckFeebas()` (pret/src/wild_encounter.c:75-118) runs on EVERY fishing
  bite while `gSaveBlock1.location` is Route 119 -- for ANY rod, because
  `FishingWildEncounter` (:598-618) asks it before it ever looks at the map's
  own fishing table. It rolls `Random() % 100 > 49` first, so a FEEBAS tile is
  a **50/50** per hooked bite and every other bite comes off the ordinary
  Route 119 rod table.

* The six spots come from a private LCG seeded by ONE SAVE FIELD:
  `FeebasSeedRng(gSaveBlock1.easyChatPairs[0].unk2)` (:101). That is the
  Dewford trend pair (include/global.h:740, `/*0x2DD4*/ struct EasyChatPair
  easyChatPairs[5]`), field `unk2` at +2 inside a 0x8-byte struct
  (include/global.h:233-240) -- so the seed is a u16 at
  `gSaveBlock1 + 0x2DD4 + 2`. `FeebasRandom` (:120-124) is
  `v = 12345 + 0x41C64E6D * v` (mod 2^32), returning `v >> 16` as a u16.

* Each draw is `% 447`, `0` is remapped to `447`, and 1/2/3 are REJECTED and
  redrawn (:104-108). 447 is the water-tile total the cartridge's own debug
  menu asserts: 131 + 167 + 149 (`FeebasDebug_GetTrueNumberOfWaterTilesInMap
  Third`, :132-141). This script recounts those three numbers off the shipped
  layout and refuses to fish if they disagree -- that is the proof that the
  tile enumeration here is the same enumeration the engine walks.

* A tile's NUMBER is its 1-based rank in a row-major scan of Route 119's water
  (`GetRoute119WaterTileNum`, :52-73): `for y in section, for x in 0..width`,
  counting tiles where `MetatileBehavior_IsFeebasEncounterable` holds -- which
  is `sTileBitAttributes & 2` (surfable) and not MB_WATERFALL
  (src/metatile_behavior.c:991-998). The section bases 0/0x83/0x12A in
  `gRoute119WaterTileData` (:25-30) are exactly the cumulative counts, so a
  global row-major rank over y=0..139 is the same number.

So: read one u16, run the LCG, and six coordinates fall out of ~440.

Fishing them uses the OLD ROD on purpose. `Fishing2`
(src/field_player_avatar.c:1536-1550) sets `tMinRoundsRequired` to
`arr1[rod] + Random() % arr2[rod]` with arr2 = {1, 3, 6}: the Old Rod always
plays exactly ONE dot round, the Super Rod up to six, and `Fishing9`'s
re-round probability table is `{0,0}` for the Old Rod. Since `CheckFeebas`
does not care which rod is in your hand, the Old Rod is strictly the fastest
way to roll the 50/50.

    scripts/feebas.py --state saves/fb2.state --compute
    scripts/feebas.py --state saves/fb2.state --out saves/fb2-out.state
"""
import argparse
import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from pokeagent.trek import Driver  # noqa: E402
from pokeagent.dex import DexTarget  # noqa: E402
from pokeagent import nav as nav_mod  # noqa: E402

log = logging.getLogger("feebas")

MAP = "Route119"
#: gRoute119WaterTileData: (yMin, yMax, base) per third
#: (pret/src/wild_encounter.c:25-30).
SECTIONS = ((0, 0x2D, 0x000), (0x2E, 0x5B, 0x083), (0x5C, 0x8B, 0x12A))
#: FeebasDebug_GetTrueNumberOfWaterTilesInMapThird (:132-141).
EXPECT = (131, 167, 149)
MOD = 447
SPOTS = 6


# ---- the cartridge's arithmetic -----------------------------------------

def feebas_seed(d) -> int:
    """`gSaveBlock1.easyChatPairs[0].unk2`, the whole input to the spots."""
    base = d.state.sb1["easyChatPairs"]          # 0x2DD4, parsed from global.h
    addr = d.emu.resolve("gSaveBlock1") + base + 2
    return d.emu.u16(addr), addr, base


def feebas_spot_numbers(seed: int) -> list[int]:
    """The six tile NUMBERS, exactly as CheckFeebas draws them."""
    value = seed & 0xFFFF
    out = []
    while len(out) != SPOTS:
        value = (12345 + 0x41C64E6D * value) & 0xFFFFFFFF
        draw = (value >> 16) & 0xFFFF
        spot = draw % MOD
        if spot == 0:
            spot = MOD
        if spot < 1 or spot >= 4:            # 1, 2 and 3 are redrawn
            out.append(spot)
    return out


def is_feebas_water(beh, cell) -> bool:
    """MetatileBehavior_IsFeebasEncounterable (metatile_behavior.c:991-998):
    surfable (sTileBitAttributes bit 1) and not MB_WATERFALL. NOT a collision
    check -- the engine counts unswimmable water too, which is why a spot can
    land on a tile no rod can be aimed at."""
    if cell is None:
        return False
    if cell.behavior == nav_mod.WATERFALL:
        return False
    return bool(beh.is_surfable(cell.behavior))


def water_tiles(d) -> tuple[list, list[int]]:
    """`[(number, x, y, cell)]` in the engine's own scan order, plus the
    per-third counts so they can be checked against the ROM's debug values."""
    beh = d.nav.beh
    grid = d.nav.grid(MAP)
    info = d.nav.info(MAP)
    tiles, counts = [], []
    number = 0
    for y_min, y_max, base in SECTIONS:
        assert base == number, f"section base {base:#x} != running count {number}"
        before = number
        for y in range(y_min, min(y_max, info.height - 1) + 1):
            for x in range(info.width):
                cell = grid[y][x]
                if is_feebas_water(beh, cell):
                    number += 1
                    tiles.append((number, x, y, cell))
        counts.append(number - before)
    return tiles, counts


def compute(d) -> dict:
    seed, addr, base = feebas_seed(d)
    tiles, counts = water_tiles(d)
    numbers = feebas_spot_numbers(seed)
    by_number = {n: (x, y, c) for n, x, y, c in tiles}
    spots = []
    for n in numbers:
        hit = by_number.get(n)
        spots.append({
            "number": n,
            "xy": None if hit is None else (hit[0], hit[1]),
            "behavior": None if hit is None else hit[2].behavior,
            "collision": None if hit is None else hit[2].collision,
        })
    return {
        "seed": seed, "seed_addr": addr, "sb1_offset": base,
        "counts": counts, "total": len(tiles), "numbers": numbers,
        "spots": spots,
    }


# ---- getting a rod in front of them --------------------------------------

def stand_cells(d, tx, ty) -> list:
    """`[(stand_x, stand_y, facing)]` for the tile, nearest first.

    A rod is cast at the tile IN FRONT, so the player stands on one of the
    four neighbours. Both shores and open water qualify -- `CanFish`
    (src/item_use.c:222-250) takes a surfing player at any elevation and an
    on-foot player at elevation 3.
    """
    px, py = d.pos()
    out = []
    for face, (dx, dy) in (("U", (0, 1)), ("D", (0, -1)),
                           ("L", (1, 0)), ("R", (-1, 0))):
        # facing F from (tx+dx, ty+dy) looks at (tx, ty)
        sx, sy = tx + dx, ty + dy
        cell = d.nav.cell(MAP, sx, sy)
        if cell is None or cell.kind == "blocked":
            continue
        out.append((sx, sy, face, abs(sx - px) + abs(sy - py)))
    out.sort(key=lambda r: r[3])
    return [(x, y, f) for x, y, f, _ in out]


def face(d, want) -> bool:
    """Turn without stepping (collect.py:403-415 learned this the hard way)."""
    if d.facing() == want:
        return True
    d.emu.run_sequence(f"{want}:4 .:12")
    return d.facing() == want


def caught_ids(d, target) -> set:
    caught, _seen = target.dex_flags(d.state)
    return caught


def policy_for(want_species, saw=None):
    """Ball the wanted fish, run from everything else.

    FEEBAS is catch rate 255 and level 20-25, so a ball is a near certainty
    and there is no reason to soften it first. Everything else on the Route
    119 rod table is already in the dex, and fleeing is the cheapest possible
    way to end its battle.

    `saw` collects the enemy species the ENGINE showed each turn, and that is
    the only trustworthy record of what a cast hooked: read straight after
    `state.battle_ready()` goes true, `battle_frame()` still returned the
    PREVIOUS battle's enemy (a cast that produced FEEBAS was logged as
    "hooked WINGULL", the mon killed two encounters earlier). The policy runs
    on a frame the battle loop built for a live turn, so it cannot be stale.
    """
    def policy(frame):
        enemy = (frame.get("enemy") or {})
        name = (enemy.get("species") or "").upper()
        if saw is not None and name and (not saw or saw[-1] != name):
            saw.append(name)
        if want_species in name:
            balls = (frame.get("bag") or {}).get("poke_balls") or {}
            for pick in ("ULTRA BALL", "GREAT BALL", "POKE BALL", "POKé BALL"):
                for held, count in balls.items():
                    if count and held.upper().replace("é", "E") == pick.replace("é", "E"):
                        return ("ball", held)
            if balls:
                return ("ball", next(iter(balls)))
            return None
        return "flee"
    return policy


def cast_at(d, sx, sy, facing, rod, casts, deadline, target, want, log_every=1):
    """Cast `casts` times at the tile ahead. Returns (casts_spent, hooked)."""
    spent = 0
    hooked = []
    for _ in range(casts):
        if time.time() > deadline:
            break
        # THE TRAP: fish() refuses with "already in a battle" forever if a
        # previous cast left one live. Answer it first, every time.
        if d.in_battle():
            d.fight(policy=policy_for(want))
        for _ in range(3):
            if not d.scene_active() and not d.in_battle():
                break
            d.advance_scene(60000)
            if d.in_battle():
                d.fight(policy=policy_for(want))
            d.close_menus()
        if d.pos() != (sx, sy):
            if not d.goto(sx, sy, on_battle="fight"):
                log.info("   lost the stand cell (%d,%d)", sx, sy)
                break
        if not face(d, facing):
            log.info("   could not face %s from (%d,%d)", facing, sx, sy)
            break
        spent += 1
        if not d.fish(rod):
            if d.last_fish_reason == "no-rod":
                raise SystemExit(f"no rod: {d.last_fish_detail}")
            if d.last_fish_reason == "wrong-tile":
                log.info("   wrong-tile: %s", d.last_fish_detail)
                break
            continue
        before = caught_ids(d, target)
        saw: list[str] = []
        d.fight(policy=policy_for(want, saw))
        name = saw[0] if saw else "?"
        hooked.append(name)
        log.info("   cast %d hooked %s", spent, name)
        after = caught_ids(d, target)
        if after - before:
            log.info("   NEW DEX FLAGS: %s", sorted(after - before))
            return spent, hooked
    return spent, hooked


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True)
    ap.add_argument("--out", default=None)
    ap.add_argument("--compute", action="store_true",
                    help="print the six tiles and exit without moving")
    ap.add_argument("--rod", default="OLD ROD")
    ap.add_argument("--casts", type=int, default=40,
                    help="casts per spot per pass")
    ap.add_argument("--budget", type=float, default=12000.0)
    ap.add_argument("--species", default="FEEBAS")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    if "line3" in args.state:
        raise SystemExit("refusing to touch the canonical line3 state")

    deadline = time.time() + args.budget
    d = Driver(args.state)
    target = DexTarget(d.emu, d.names, d.consts, d.nav, spec=d.spec)

    info = compute(d)
    log.info("seed = %#06x (u16 at %#010x = gSaveBlock1 + %#x + 2, "
             "gSaveBlock1.easyChatPairs[0].unk2)",
             info["seed"], info["seed_addr"], info["sb1_offset"])
    log.info("water tiles per third: %s (ROM debug says %s), total %d",
             info["counts"], list(EXPECT), info["total"])
    for s in info["spots"]:
        log.info("  spot #%3d -> %s  behavior=%s collision=%s",
                 s["number"], s["xy"], s["behavior"], s["collision"])
    if tuple(info["counts"]) != EXPECT or info["total"] != MOD:
        raise SystemExit(
            f"tile enumeration disagrees with the ROM: {info['counts']} "
            f"vs {list(EXPECT)} -- refusing to fish a wrong map")
    if args.compute:
        return 0

    want = args.species.upper()
    natdex = None
    for entry in getattr(target, "entries", ()) or ():
        for attr in ("rom_name", "name", "species_name"):
            if str(getattr(entry, attr, "")).upper() == want:
                natdex = getattr(entry, "natdex", None)
    before_caught = caught_ids(d, target)
    log.info("dex before: %d caught (looking for %s, natdex %s)",
             len(before_caught), want, natdex)
    # INSTALL THE POLICY ON THE DRIVER, not just per fight() call.
    # `battle_policy` (trek.py:3159) is the only hook this package consults:
    # every wild battle that INTERRUPTS a journey is fought by goto()/travel()
    # calling fight() with no policy at all, so a policy passed only to my own
    # fight() calls is absent for exactly those encounters. FEEBAS is
    # fishing-only so nothing wanted can appear mid-walk here, but leaving the
    # driver's default unset means the next species this script is pointed at
    # would be KO'd by tactics on the way to the water.
    # `encounter_policy` is Crystal's name and has no consumer in this package.
    d.battle_policy = policy_for(want)

    if d.map_name() != MAP:
        # FLY FIRST. Route 119 is eight maps from Oldale on foot and `travel`
        # walks every one of them; Fortree City sits on Route 119's northern
        # seam, which is also the third the y=40-47 spots are in.
        log.info("travelling to %s from %s %s", MAP, d.map_name(), d.pos())
        try:
            d.fly_to("FortreeCity")
        except Exception as exc:  # noqa: BLE001 - walking is still an option
            log.info("fly refused (%s); walking", str(exc)[:80])
        for attempt in range(4):
            try:
                d.travel(MAP, on_battle="fight")
                break
            except Exception as exc:  # noqa: BLE001
                log.info("travel attempt %d: %s", attempt + 1, str(exc)[:120])
                if d.in_battle():
                    d.fight(policy=policy_for(want))
        if d.map_name() != MAP:
            raise SystemExit(f"could not reach {MAP}: sitting on "
                             f"{d.map_name()} {d.pos()}")
    log.info("on %s at %s", d.map_name(), d.pos())

    total_casts = 0
    hooked_all = []
    unreachable = []
    passes = 0
    got = False
    # De-duplicate: the draw can repeat a number.
    order = []
    for s in info["spots"]:
        if s["xy"] and s["xy"] not in order:
            order.append(s["xy"])
    while time.time() < deadline and not got:
        passes += 1
        # THE SEED CAN MOVE UNDER US. `easyChatPairs[0]` is the Dewford
        # TREND, and the trend updates per day (UpdateDewfordTrendPerDay) --
        # which is the folklore "Feebas moves when the trend changes",
        # restated as the one field CheckFeebas actually seeds from. A run
        # that crosses an RTC midnight would otherwise keep fishing six dead
        # tiles, so re-read it and recompute rather than trust pass 1.
        fresh = compute(d)
        if fresh["seed"] != info["seed"]:
            log.info("SEED CHANGED %#06x -> %#06x; new spots %s",
                     info["seed"], fresh["seed"],
                     [s["xy"] for s in fresh["spots"]])
            info = fresh
            order = []
            for s in info["spots"]:
                if s["xy"] and s["xy"] not in order:
                    order.append(s["xy"])
        for (tx, ty) in order:
            if time.time() > deadline or got:
                break
            placed = False
            for (sx, sy, facing) in stand_cells(d, tx, ty):
                if time.time() > deadline:
                    break
                log.info("[spot %s] standing (%d,%d) facing %s",
                         (tx, ty), sx, sy, facing)
                if d.pos() != (sx, sy) and not d.goto(sx, sy, on_battle="fight"):
                    log.info("   no route to (%d,%d)", sx, sy)
                    continue
                if not face(d, facing):
                    continue
                ok, why = d.fishing.faces_fishable_water()
                if not ok:
                    log.info("   not fishable: %s", why)
                    continue
                placed = True
                spent, hooked = cast_at(d, sx, sy, facing, args.rod,
                                        args.casts, deadline, target, want)
                total_casts += spent
                hooked_all += hooked
                if caught_ids(d, target) - before_caught:
                    got = True
                break
            if not placed:
                unreachable.append((tx, ty))
                log.info("[spot %s] no usable stand cell this pass", (tx, ty))
        if args.out and not d.scene_active() and not d.in_battle():
            d.save(args.out)
        log.info("pass %d done: %d casts, hooked %s", passes, total_casts,
                 sorted(set(hooked_all)))

    for _ in range(6):
        if not d.scene_active():
            break
        d.emu.run_sequence("B:4 .:30")
    if d.in_battle():
        d.fight(policy=policy_for(want))
    after = caught_ids(d, target)
    log.info("dex after: %d caught (+%d): new %s",
             len(after), len(after - before_caught),
             sorted(after - before_caught))
    log.info("casts %d, hooked %s, unreachable spots %s",
             total_casts, sorted(set(hooked_all)), sorted(set(unreachable)))
    if args.out:
        d.save(args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
