#!/usr/bin/env python
"""CROBAT and SHEDINJA: the two entries no amount of levelling alone produces.

A CHAIN LEG. Takes `--state PATH`, mutates that file in place, saves after
every species it lands, reads the live dex first and skips anything already
flagged CAUGHT, and works from wherever the save happens to stand.

SHEDINJA is not an evolution -- it is a SIDE EFFECT of one. When a mon whose
FIRST evolution row is `EVO_LEVEL_NINJASK` evolves, the scene calls
`CreateShedinja` (pret/src/evolution_scene.c:493, called from :685), which
copies the mon into `gPlayerParty[gPlayerPartyCount]`, overwrites its species
with the SECOND row of the table (`[SPECIES_NINCADA] =
{{EVO_LEVEL_NINJASK, 20, SPECIES_NINJASK}, {EVO_LEVEL_SHEDINJA, 20,
SPECIES_SHEDINJA}}`, pret/src/data/pokemon/evolution.h:144-145) and then sets
the SEEN and CAUGHT dex flags itself (evolution_scene.c:519-520). The whole
precondition is one line, :497:

    if (gEvolutionTable[preEvoSpecies][0].method == EVO_LEVEL_NINJASK
        && gPlayerPartyCount < 6)

so Gen 3 asks for A FREE PARTY SLOT AND NOTHING ELSE -- there is no Poke Ball
check anywhere in the function (that arrives in Gen 4). Practically: a full
party costs the entry SILENTLY, with no message at all. This leg therefore
levels the NINCADA with a party of FIVE and never six, and the NINJASK already
in the boxes is worthless -- the SHEDINJA is minted by the evolution event, not
by owning the line.

CROBAT wants `friendship >= 220` at a LEVEL-UP (pret/src/pokemon_3.c:298, the
EVO_FRIENDSHIP arm), and the boxed GOLBAT sits at the wild-caught base of 70.
The deltas are tiered by current friendship (pokemon_3.c:652-661):

    { 5,  3,   2}, // FRIENDSHIP_EVENT_GROW_LEVEL
    { 5,  3,   2}, // FRIENDSHIP_EVENT_VITAMIN
    { 1,  1,   1}, // FRIENDSHIP_EVENT_WALKING   (and only on a coin flip,
                                                  `!(Random() & 1)`, :691)

Levels are the obvious lever and the wrong one: 70 -> 220 is 49 level-ups.
Walking is worse -- `UpdateHappinessStep` fires one event per 128 steps
(field_control_avatar.c:604-620) and the coin flip halves it, so ~19,000 steps
even with the bonus below. Vitamins pay the same 5/3/2 and cost only money,
and this save is at the 999,999 cap against 9,800 a bottle from Slateport's
Energy Guru (pret/data/maps/SlateportCity/scripts.inc:52-64 sells all six).

WHERE you bottle matters, which is the one trick here. Every positive
friendship event pays ONE MORE POINT while you stand on the mon's OWN met
location -- `if (GetMonData(mon, MON_DATA_MET_LOCATION, 0) ==
sav1_map_get_name()) friendship++` (pokemon_3.c:707-708), and the vitamin path
has the same clause (pokemon_item_effect.c:484-485). This GOLBAT reads
met_location=72 = MAPSEC_CAVE_OF_ORIGIN, so bottling INSIDE the Cave of Origin
turns 5/3/2 into 6/4/3: 70 -> 100 in five bottles, 100 -> 200 in twenty-five,
200 -> 224 in eight. Thirty-eight, against a hard ceiling of fifty-one --
`PokemonUseItemEffects` pays no friendship once `GetMonEVCount >= 510`
(pokemon_item_effect.c:251) or that one stat is at 100 (:254), because the
friendship arm is gated on the EV having actually applied (`retVal == 0`,
:469). Outside the cave the same climb is 48 bottles, which does not fit under
the ceiling with any margin. This GOLBAT has 0 EVs in all six stats, so the
whole allowance is available -- and every bottle is READ BACK off the mon
rather than counted, because the ceiling is close enough that guessing and
failing look identical.

Friendship alone does not evolve anything: the check only runs at a level-up.
So the last step for both species is the same one -- the EXP. SHARE on a
BENCHED holder, which is paid `expYield * level / 7 / 2` per knockout whether
or not it was sent out (battle_script_commands.c:3381-3441) and still sets
`gLeveledUpInBattle`, so the post-battle pass evolves it. GOLBAT is L35 with
exp 42875 = 35^3 (MEDIUM_FAST), so one level is 3,781 exp -- about 33 wild
Zubat in the cave it is already standing in, no travel at all. NINCADA is
ERRATIC and L20 costs 12,800, which is one Elite Four pass (~25 knockouts of
L46-57) and nothing less, so that one goes to the League.
"""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from pokeagent.trek import Driver, TravelInterrupted  # noqa: E402
from pokeagent.dex import DexTarget  # noqa: E402
from pokeagent.storage import Storage  # noqa: E402
from pokeagent.teaching import Teacher  # noqa: E402
from pokeagent.mart import Mart  # noqa: E402

import share_grind as sg  # noqa: E402  -- to_center/unwedge/holder, all proven

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("cs2")

CROBAT_DEX, SHEDINJA_DEX = 169, 292
SHARE_ID, SHARE = 182, "EXP SHARE"   # no period: "EXP. SHARE" resolves to None
LEAD = "PELIPPER"                    # the L100 that sweeps and earns nothing
#: Withdrawn in this order to stand IN FRONT of the holder. Every escort is
#: one more faint the gauntlet has to get through before the holder is dragged
#: in, and a fainted holder earns nothing for the rest of the run
#: (battle_script_commands.c:3361-3364).
ESCORTS = ("BLAZIKEN", "AGGRON", "MIGHTYENA", "NINJASK", "SWELLOW", "LINOONE")
VITAMINS = ("HP UP", "PROTEIN", "IRON", "CARBOS", "CALCIUM", "ZINC")
BOTTLE_EV = {"HP UP": "hp", "PROTEIN": "attack", "IRON": "defense",
             "CARBOS": "speed", "CALCIUM": "sp_attack", "ZINC": "sp_defense"}
GURU = (5, 47)                       # SlateportCity_EventScript_EnergyGuru
#: Stop bottling here rather than at 220. The level-up that evolves adds its
#: own +2 first (AdjustFriendship(GROW_LEVEL) runs before the evolution pass),
#: but 224 costs two bottles more and removes the need to be right about the
#: order of two ROM calls.
FRIENDSHIP_GOAL = 224


# ---- reading -------------------------------------------------------------

def party(d):
    return [m for m in d.state.party() if not m.is_egg]


def spn(d, m):
    return d.names.species(m.species).upper()


def holder(d):
    for i, m in enumerate(d.state.party()):
        if not m.is_egg and m.held_item == SHARE_ID:
            return i
    return None


def fresh_dex(d):
    return DexTarget(d.emu, d.names, d.consts, d.nav, spec=d.spec)


def caught_set(d):
    return fresh_dex(d).dex_flags(d.state)[0]


def slot_of(d, species):
    for i, m in enumerate(party(d)):
        if spn(d, m) == species:
            return i
    return None


def mon_of(d, species):
    i = slot_of(d, species)
    return party(d)[i] if i is not None else None


def boxed_find(d, species):
    """`(box, slot, mon)` for a boxed species, re-read every call.

    Never cached: a deposit shifts nothing but a withdraw does, and a stale
    slot number withdraws the wrong mon without complaining.
    """
    for slot, mo in fresh_dex(d).boxed():
        if d.names.species(mo.species).upper() == species:
            return slot // 30, slot % 30, mo
    return None, None, None


def show(d, label=""):
    log.info("party%s: %s", f" {label}" if label else "",
             [(spn(d, m), m.level, m.friendship, m.held_item)
              for m in party(d)])


# ---- staging -------------------------------------------------------------

def emerge(d, tries=10) -> bool:
    """Come UP from an Underwater map, walking to a surfacable ceiling.

    `flight.step_outside` only knows warps (flying.py:850), and an Underwater
    map has none -- so from the canonical parking spot it answered False
    immediately and this leg exited with "cannot reach open ground from
    Underwater1". Surfacing is a FIELD MOVE: `TrySetupDiveEmergeScript` hangs
    off the B button and needs the tile the player is STANDING on to have a
    surfacable ceiling (trek.py:1732-1747), so a refusal means "wrong tile",
    not "impossible" -- walk to another one and press again.
    """
    from pokeagent import nav as nav_mod

    if not d.underwater():
        return True
    if not d.can_dive():
        log.info("  cannot surface: no DIVE knower in the party")
        return False
    here = d.map_name()
    if d.dive():
        return True
    log.info("  surface refused here: %s", getattr(d, "last_field_reason", "?"))
    px, py = d.pos()
    try:
        reach = d.nav.reachable(here, (px, py), d.elevation())
    except Exception as exc:  # noqa: BLE001
        log.info("  reachable(%s): %s", here, str(exc)[:70])
        return False
    spots = []
    for c in reach:
        cell = d.nav.cell(here, *c)
        if cell is not None and cell.behavior not in nav_mod.NO_SURFACING:
            spots.append(c)
    spots.sort(key=lambda c: abs(c[0] - px) + abs(c[1] - py))
    log.info("  %d surfacable cells on %s", len(spots), here)
    for c in spots[:tries]:
        try:
            if not d.goto(*c, on_battle="fight"):
                continue
        except Exception:  # noqa: BLE001
            continue
        if d.dive():
            log.info("  surfaced from %s at %s", here, c)
            return True
    return not d.underwater()


def surface(d) -> bool:
    """Get somewhere Fly accepts, from wherever the leg inherited.

    The canonical save is parked at `Underwater1`, and
    `Overworld_MapTypeAllowsTeleportAndFly` refuses MAP_TYPE_UNDERWATER: the
    first run of this leg answered "fly: indoors -- Underwater1" and exited
    with no NINCADA. A chain leg cannot assume a map, so every fly in this
    file goes through here.
    """
    sg.unwedge(d)
    emerge(d)
    for _ in range(8):
        if d.flight.flyable_here():
            return True
        try:
            if not d.flight.step_outside():
                break
        except Exception:  # noqa: BLE001
            break
        sg.unwedge(d)
    return d.flight.flyable_here()


def to_center(d) -> bool:
    """Stand in a Pokemon Center.

    FLY TO A CITY FIRST when we are out in the wild. `heal_at_nearest_center`
    walks, and from the sea route this leg emerges onto it answered "could not
    cross the L seam to LilycoveCity" and then spent minutes fighting
    TENTACOOL on the way to nowhere. Mauville is the most connected landing on
    the board and its Centre is a few steps from the Fly tile.
    """
    if d.map_name().endswith("PokemonCenter_1F"):
        return True
    if surface(d) and not d.map_name().endswith("City"):
        try:
            d.fly_to("MauvilleCity")
        except Exception as exc:  # noqa: BLE001
            log.info("fly to Mauville: %s", str(exc)[:70])
    return sg.to_center(d)


def free_the_share(d, st, t, target=None) -> bool:
    """Get the EXP. SHARE into the BAG, whoever is wearing it.

    An item stays on a mon that is deposited and leaves the bag with it, so
    this has to happen while the wearer is still in the party -- and the party
    picker's geometry is only trustworthy on a short party, so shrink to the
    wearer plus the lead first.

    Already on the mon we want? Leave it there. This leg is re-run on the same
    save (it is a chain leg), and taking the share off the target only to give
    it straight back is two of the slowest menu drives in the run for nothing.
    """
    h = holder(d)
    if h is None:
        return True
    worn_by = spn(d, d.state.party()[h])
    if target is not None and worn_by == target:
        log.info("%s already holds the share", target)
        return True
    keep = {worn_by, LEAD}
    shrink(d, st, keep, floor=2)
    h = holder(d)
    if h is None:
        return True
    if not t.take_from_mon(h):
        log.info("could not unequip the share: %s",
                 getattr(t, "last_reason", "?"))
        return False
    log.info("share is in the bag")
    return True


def shrink(d, st, keep, floor=1) -> None:
    for _ in range(8):
        p = party(d)
        if len(p) <= floor:
            return
        drop = next((i for i, m in enumerate(p) if spn(d, m) not in keep),
                    None)
        if drop is None or not st.deposit(drop):
            return


def ensure_in_party(d, st, species) -> bool:
    if slot_of(d, species) is not None:
        return True
    box, slot, _ = boxed_find(d, species)
    if box is None:
        log.info("%s is in neither party nor boxes", species)
        return False
    while len(party(d)) >= 6:
        v = next((i for i, m in enumerate(party(d))
                  if spn(d, m) not in (LEAD, species)), None)
        if v is None or not st.deposit(v):
            break
    if not st.withdraw(box, slot):
        log.info("withdraw %s refused: %s", species,
                 getattr(st, "last_reason", "?"))
        return False
    return slot_of(d, species) is not None


def pair_up(d, st, target) -> bool:
    """Party == exactly [LEAD, target], which is the only shape the give
    has ever landed correctly on this harness (share_grind.py:248-254)."""
    if not (ensure_in_party(d, st, LEAD) and ensure_in_party(d, st, target)):
        return False
    # The L100 must LEAD: a L6 holder in front of Sidney is knocked out on
    # turn one and a fainted mon earns nothing.
    for _ in range(8):
        p = party(d)
        if p and spn(d, p[0]) == LEAD:
            break
        drop = next((i for i, m in enumerate(p) if spn(d, m) != LEAD), None)
        if drop is None or len(p) <= 1 or not st.deposit(drop):
            break
    if slot_of(d, target) is None and not ensure_in_party(d, st, target):
        return False
    shrink(d, st, {LEAD, target}, floor=2)
    p = party(d)
    ok = len(p) == 2 and spn(d, p[0]) == LEAD and spn(d, p[1]) == target
    if not ok:
        log.info("could not reduce to [%s, %s]: %s", LEAD, target,
                 [spn(d, m) for m in p])
    return ok


def give_share(d, t, target) -> bool:
    idx = next((i for i, m in enumerate(d.state.party())
                if spn(d, m) == target), None)
    if idx is None:
        log.info("%s is not in the party", target)
        return False
    if d.state.party()[idx].held_item == SHARE_ID:
        return True
    # BY SPECIES, NOT BY NICKNAME. `Teacher` matches either
    # (teaching.py:732-736), and a mon caught by this leg has NO nickname --
    # declining the naming prompt logs "accepted the default name ''", so
    # passing `mon.nickname` hands the matcher an empty string.
    #
    # FROM THE FIELD MENU FIRST. The bag's own give flow has a party cursor
    # this project has never read correctly -- a share aimed at LOUDRED landed
    # on the level-100 and four laps paid nobody.
    if not (t.give_to_mon(SHARE, target) or t.give_from_field(idx, SHARE)):
        log.info("could not give the share: %s", getattr(t, "last_reason", "?"))
        return False
    h = holder(d)
    if h is None or spn(d, d.state.party()[h]) != target:
        log.info("share is on %s, not %s", spn(d, d.state.party()[h])
                 if h is not None else "nobody", target)
        return False
    log.info("share landed on slot %d (%s)", h, spn(d, d.state.party()[h]))
    return True


def bench_behind_escorts(d, st, target, size=5) -> bool:
    """Re-order to [LEAD, escorts..., target] with `size` mons total.

    Two constraints pull against each other: the give only lands on a two-mon
    party, and the holder wants bodies in front of it. So the give happens
    first and the ORDER is fixed afterwards by depositing the holder and
    withdrawing it LAST -- a deposited mon keeps its held item, so the share
    travels with it. `size` is 5 and never 6 for a NINCADA: `CreateShedinja`
    needs `gPlayerPartyCount < 6` (evolution_scene.c:497) and a sixth escort
    would cost the entry with no message at all.
    """
    held = mon_of(d, target)
    if held is None:
        return False
    if held.held_item != SHARE_ID:
        log.info("%s is not holding the share -- refusing to bench it", target)
        return False
    i = slot_of(d, target)
    if not st.deposit(i):
        log.info("could not park %s while adding escorts: %s", target,
                 getattr(st, "last_reason", "?"))
        return False
    for species in ESCORTS:
        if len(party(d)) >= size - 1:
            break
        if slot_of(d, species) is not None:
            continue
        box, slot, _ = boxed_find(d, species)
        if box is None:
            continue
        if st.withdraw(box, slot):
            log.info("escort %s added", species)
    box, slot, _ = boxed_find(d, target)
    if box is None or not st.withdraw(box, slot):
        log.info("could not bring %s back", target)
        return False
    p = party(d)
    if spn(d, p[-1]) != target:
        log.info("%s is not last: %s", target, [spn(d, m) for m in p])
    if p[-1].held_item != SHARE_ID:
        log.info("%s lost the share in the PC -- regiving", target)
        return False
    if len(p) >= 6 and target != "GOLBAT":
        log.info("party is FULL (%d) -- SHEDINJA would be dropped silently",
                 len(p))
        return False
    show(d, "benched")
    return True


# ---- walking / fighting --------------------------------------------------

def sweep_policy(frame):
    """Strongest move with PP, never switch.

    `tactics.recommend` ranks a resist-switch above damage, which fronts the
    benched holder and gets it knocked out -- and a fainted mon is skipped by
    both the participant count and the exp loop.
    """
    best, score = 0, -1.0
    for i, mv in enumerate((frame or {}).get("moves") or []):
        if not mv or not mv.get("pp"):
            continue
        s = (mv.get("power") or 0) * (mv.get("effect_mult") or 1.0)
        if s > score:
            best, score = i, s
    return ("attack", best)


def pace(d, policy, stop, minutes=25.0, terrain="grass") -> bool:
    """Walk encounter terrain until `stop()`, fighting with `policy`.

    `goto` does the walking: `step_dir` returns False for free while a scene
    owns input, and a hand-stepped loop once spun 7.5 million times in 150
    seconds without moving.
    """
    d.battle_policy = policy
    cells = set(d.nav.find_tiles(d.map_name(), terrain))
    if not cells:
        cells = set(d.nav.reachable(d.map_name(), d.pos(), d.elevation()))
    reach = set(d.nav.reachable(d.map_name(), d.pos(), d.elevation()))
    px, py = d.pos()
    spots = sorted(cells & reach, key=lambda c: abs(c[0] - px) + abs(c[1] - py))
    if not spots:
        log.info("no reachable %s on %s", terrain, d.map_name())
        return False
    log.info("pacing %s: %d cells", d.map_name(), len(spots))
    end = time.time() + minutes * 60.0
    i, stalled, fights = 0, 0, 0
    while time.time() < end and not stop():
        if d.scene_active():
            d.advance_scene(40_000)
        i += 1
        target = spots[(i * 7) % len(spots)]
        if target == d.pos():
            continue
        try:
            d._journey_deadline = min(end, time.time() + 45.0)
            stalled = 0 if d.goto(*target, on_battle="raise") else stalled + 1
        except TravelInterrupted:
            d.fight(policy=policy)
            d.advance_scene(20_000)
            fights += 1
            stalled = 0
            if fights % 5 == 0:
                log.info("  %d battles, party %s", fights,
                         [(m.nickname, m.level, m.hp) for m in party(d)])
        except Exception as exc:  # noqa: BLE001
            log.info("pace: %s", str(exc)[:80])
            stalled += 1
        if stalled >= 8:
            log.info("pacing stalled: %s", getattr(d, "last_goto_reason", "?"))
            break
    # `_journey_deadline` is per-DRIVER, not per-call: one left behind makes
    # every later take_warp refuse its approach cell.
    d._journey_deadline = None
    log.info("paced %s: %d battles, stop=%s", d.map_name(), fights, stop())
    return stop()


# ---- SHEDINJA ------------------------------------------------------------

def hunt_nincada(d, minutes=25.0) -> bool:
    """Catch a NINCADA on Route 116 (20% of the land table across two slots).

    Not `Catcher`: NINCADA's caught flag is already set, and the catcher
    declines anything already owned -- correctly, for its own job. This wants
    the MON, not the entry.
    """
    if slot_of(d, "NINCADA") is not None:
        return True
    if boxed_find(d, "NINCADA")[0] is not None:
        return True
    if d.map_name() != "Route116":
        if not surface(d):
            log.info("cannot reach open ground from %s", d.map_name())
            return False
        if not d.fly_to("RustboroCity"):
            log.info("could not fly to Rustboro")
            return False
        sg.unwedge(d)
        try:
            d.travel("Route116", on_battle="fight", budget_s=240)
        except TravelInterrupted:
            d.fight(policy=sweep_policy)
        except Exception as exc:  # noqa: BLE001
            log.info("travel Route116: %s", str(exc)[:90])
    if d.map_name() != "Route116":
        log.info("not on Route116 (on %s)", d.map_name())
        return False

    balls = d.state.bag().get("poke_balls") or {}
    ball = ("ULTRA BALL" if balls.get("ULTRA BALL") else
            "GREAT BALL" if balls.get("GREAT BALL") else
            next(iter(balls), None))
    if ball is None:
        log.info("no balls in the bag")
        return False
    log.info("hunting NINCADA with %s (%s)", ball, balls)

    def enemy_now():
        """The wild mon, read out of `gEnemyParty[0]` DIRECTLY.

        `state.enemy_party()` is unusable in a wild battle and it fails
        SILENTLY: it sizes the read with `gEnemyPartyCount`
        (pokeagent/state.py:282-283), and the only writer of that variable is
        `CalculateEnemyPartyCount` (pret/src/pokemon_2.c:1025-1033), which the
        wild-battle setup never calls -- so it reads 0, the list comes back
        empty, and a policy built on it silently falls through to its default.
        Measured: this hunt attacked with HYDRO PUMP for five encounters and
        killed the NINCADA it was sent to catch ("NINCADA 21->0").

        `gEnemyParty[0]` itself is written by `CreateWildMon` before the first
        frame, so it is right from the start -- unlike `battle_frame()`'s
        enemy, which is `gBattleMons[1]` and can still hold the PREVIOUS
        encounter. The frame is only a fallback here.
        """
        try:
            foes = d.state._read_party("gEnemyParty", 1)
            if foes and foes[0].species:
                return d.names.species(foes[0].species).upper()
        except Exception:  # noqa: BLE001
            pass
        try:
            return ((d.battle_frame().get("enemy") or {}).get("species")
                    or "").upper()
        except Exception:  # noqa: BLE001
            return ""

    def policy(frame):
        name = enemy_now()
        if name == "NINCADA" or not name:
            # A BALL IS THE SAFE DEFAULT when the species cannot be read.
            # Throwing at a ZIGZAGOON costs one of seventy-five balls and a
            # box slot out of 298; attacking a NINCADA costs the entry.
            return ("ball", ball)
        return "flee"

    def got():
        return (slot_of(d, "NINCADA") is not None
                or boxed_find(d, "NINCADA")[0] is not None)

    return pace(d, policy, got, minutes=minutes, terrain="grass")


def park_in_hall(d) -> bool:
    try:
        surface(d)
        d.fly_to("EverGrandeCity")
        import league_loop
        league_loop.into_hall(d)
    except Exception as exc:  # noqa: BLE001
        log.info("into_hall: %s", str(exc)[:100])
    log.info("parked at %s %s", d.map_name(), d.pos())
    return d.map_name().startswith("EverGrandeCity")


def gauntlet(state, feed, minutes) -> int:
    """One Elite Four pass, in a SUBPROCESS.

    It has to be a subprocess: `elite_four.py` is the proven sweeper and it
    owns the save file for the duration, so this leg saves, hands the file
    over and re-opens it afterwards. `--feed` is unique per leg because
    `LiveFeed._claim` HARD-ERRORS when another live process owns the name, and
    the shared default is owned by the grind engine.
    """
    args = [sys.executable, "scripts/elite_four.py", "--state", state,
            "--protect-bench", "--minutes", str(minutes), "--feed", feed]
    try:
        return subprocess.run(args, cwd=str(ROOT),
                              timeout=(minutes + 6) * 60).returncode
    except subprocess.TimeoutExpired:
        log.info("gauntlet timed out")
        return 1


#: Where a benched holder is levelled: L36-40 land table at encounter rate 10
#: (pret/src/data/wild_encounters.json, VictoryRoad_1F_Sapphire), one warp
#: from the EverGrandeCity Fly tile -- `nav.exits("EverGrandeCity")` lists
#: warps at (18,41) and (18,27) both landing on VictoryRoad_1F.
GRIND_MAP = "VictoryRoad_1F"
GRIND_WARP = (18, 41)


def level_on_wilds(d, who, minutes) -> bool:
    """Pace Victory Road until the benched `who` levels up and evolves.

    Wilds rather than the League because a wild map cannot take the holder
    off the bench: the level-100 lead one-shots a L38 GOLBAT or LAIRON, never
    faints, and `_forced_switch` therefore never runs.
    """
    if slot_of(d, who) is None:
        log.info("%s is not in the party", who)
        return False

    # REVIVE IT FIRST. A fainted mon is skipped by the exp loop
    # (battle_script_commands.c:3436), so a KO'd holder paces all day and
    # earns nothing -- and this leg inherits exactly that when a previous lap
    # fronted it (`party SEA BIRD 288/296 GOLBAT 0/110`).
    m = mon_of(d, who)
    if (m.hp or 0) <= 0:
        log.info("%s is fainted -- healing before the grind", who)
        if not to_center(d):
            log.info("could not reach a Centre to revive %s", who)
            return False
        m = mon_of(d, who)
        if (m.hp or 0) <= 0:
            log.info("%s is still fainted after the Centre", who)
            return False
        log.info("%s revived at %s (%s/%s)", who, d.map_name(), m.hp, m.max_hp)

    if d.map_name() != GRIND_MAP:
        if not surface(d):
            log.info("cannot reach open ground from %s", d.map_name())
            return False
        if d.map_name() != "EverGrandeCity" and not d.fly_to("EverGrandeCity"):
            log.info("could not fly to Ever Grande")
            return False
        sg.unwedge(d)
        try:
            d.travel(GRIND_MAP, on_battle="fight", budget_s=300)
        except TravelInterrupted:
            d.fight(policy=sweep_policy)
        except Exception as exc:  # noqa: BLE001
            log.info("travel %s: %s", GRIND_MAP, str(exc)[:90])
        if d.map_name() != GRIND_MAP:
            try:
                d.take_warp(*GRIND_WARP)
            except Exception as exc:  # noqa: BLE001
                log.info("take_warp%s: %s", GRIND_WARP, str(exc)[:90])
    if not d.map_name().startswith("VictoryRoad"):
        log.info("not on Victory Road (on %s)", d.map_name())
        return False
    before = mon_of(d, who)
    log.info("levelling %s L%s exp=%s (holding %s) on %s", who, before.level,
             before.experience, before.held_item, d.map_name())
    return pace(d, sweep_policy, lambda: slot_of(d, who) is None,
                minutes=minutes, terrain="cave")


# ---- CROBAT --------------------------------------------------------------

def buy_vitamins(d, each=10) -> bool:
    """Ten of each bottle from Slateport's Energy Guru.

    Ten is the per-stat ceiling (10 EV a bottle, applied only while that stat
    is under 100, pokemon_item_effect.c:254), so ten of each is every bottle
    one mon can legally take.
    """
    have = d.state.bag().get("items") or {}
    if sum(have.get(v, 0) for v in VITAMINS) >= 40:
        log.info("bottles already in the bag: %s",
                 {v: have.get(v, 0) for v in VITAMINS})
        return True
    if d.map_name() != "SlateportCity":
        if not surface(d):
            log.info("cannot reach open ground from %s", d.map_name())
            return False
        if not d.fly_to("SlateportCity"):
            log.info("could not fly to Slateport")
            return False
    sg.unwedge(d)
    gx, gy = GURU
    if not (d.goto(gx, gy + 1) or d.goto(gx - 1, gy) or d.goto(gx + 1, gy)):
        log.info("could not reach the Energy Guru at %s: %s", GURU,
                 getattr(d, "last_goto_reason", "?"))
        return False
    d.talk_to(gx, gy)
    d.settle(600)
    mart = Mart(d)

    # PRESS THROUGH THE GURU'S MESSAGE UNTIL HIS OWN LIST IS UP.
    #
    # `Mart.is_open()` is `itemCount != 0 and itemList looks like a pointer and
    # a scene is active` (mart.py:65-83), and `itemCount` SURVIVES the last
    # shop closing -- mart.py:68-70 says so itself, because it is only rebuilt
    # when the next mart is CREATED. The guru's script messages first and
    # `pokemart`s second (pret/data/maps/SlateportCity/scripts.inc:42-47), so
    # while his text box is up the predicate is already True and `items()`
    # returns the PREVIOUS mart. Measured: standing on (5,48) in front of him,
    # this read "ULTRA BALL, NET BALL, DIVE BALL, HYPER POTION, ..." -- the
    # Poke Mart's shelf, from a shop closed long ago -- and then refused all
    # six bottles with "HP UP is not sold here".
    #
    # The honest test is the STOCK, so wait for a list that actually contains
    # what this counter is supposed to sell.
    stock = []
    for _ in range(24):
        stock = [r["name"] for r in mart.items()]
        if any(v in stock for v in VITAMINS):
            break
        d.emu.run_sequence("A:6 .:60")
        d.settle(400)
    if not any(v in stock for v in VITAMINS):
        log.info("the Energy Guru's shop never opened (stock reads %s)", stock)
        return False
    log.info("stock: %s", stock)
    for v in VITAMINS:
        short = each - (d.state.bag().get("items") or {}).get(v, 0)
        if short > 0 and not mart.buy(v, short):
            log.info("buying %s failed: %s", v, mart.last_reason)
    mart.leave()
    d.settle(600)
    sg.unwedge(d)
    bag = d.state.bag().get("items") or {}
    log.info("bottles: %s", {v: bag.get(v, 0) for v in VITAMINS})
    return sum(bag.get(v, 0) for v in VITAMINS) >= 40


def to_cave(d) -> bool:
    """Stand inside the Cave of Origin -- GOLBAT's met location, worth +1 on
    every friendship event (pokemon_3.c:707-708, pokemon_item_effect.c:484)."""
    if d.map_name().startswith("CaveOfOrigin"):
        return True
    sg.unwedge(d)
    if not d.map_name().startswith("Sootopolis"):
        if not surface(d):
            log.info("cannot reach open ground from %s", d.map_name())
            return False
        if not d.fly_to("SootopolisCity"):
            log.info("could not fly to Sootopolis")
            return False
    sg.unwedge(d)
    try:
        d.travel("CaveOfOrigin_Entrance", on_battle="fight", budget_s=240)
    except TravelInterrupted:
        d.fight(policy=sweep_policy)
    except Exception as exc:  # noqa: BLE001
        log.info("travel CaveOfOrigin_Entrance: %s", str(exc)[:90])
    if not d.map_name().startswith("CaveOfOrigin"):
        # The cave mouth is a plain warp; standing on one does not fire it.
        try:
            d.take_warp(31, 16)
        except Exception as exc:  # noqa: BLE001
            log.info("take_warp(31,16): %s", str(exc)[:90])
    log.info("now on %s %s", d.map_name(), d.pos())
    return d.map_name().startswith("CaveOfOrigin")


def bottle(d, t, who="GOLBAT", goal=FRIENDSHIP_GOAL) -> bool:
    """Feed bottles, reading the mon back after every call.

    Three outcomes have to be told apart and only the mon's own fields can do
    it: the EV moved (it landed, and the friendship rode along, because that
    arm is gated on the EV having applied, pokemon_item_effect.c:469); no EV
    and no bottle (refused -- retire the flavour if that stat is at 100); no
    EV but the bottle is gone (`_pick_party_member` landed on another slot --
    retry the same flavour).

    MEASURED, and not what the loop was written for: ONE `use_on_mon` call
    drains the WHOLE STACK and then keeps going into the next flavour. Its
    tail presses A until the species changes or the bag count drops
    (teaching.py:983-992), and a vitamin's "used on" flow returns to the bag
    with the cursor still on the item, so every press is another bottle. One
    call reported `HP UP friendship 70 -> 255, hp 0 -> 100, bag 10 -> 0` and
    left EVs of 100/100/100/100/100/10 = 510, the hard cap
    (pokemon_item_effect.c:251). That is the goal reached in one call rather
    than thirty-eight, so this leg takes it -- but it means the vitamin pocket
    is EMPTY afterwards, which is why the caller asks the mon's friendship and
    not the bag before deciding to shop.
    """
    idx = slot_of(d, who)
    if idx is None:
        log.info("%s is not in the party", who)
        return False
    met = d.map_name().startswith("CaveOfOrigin")
    log.info("bottling %s on %s (met bonus %s), friendship=%d", who,
             d.map_name(), "ACTIVE" if met else "NO", party(d)[idx].friendship)
    dead, fed, wasted = set(), 0, 0
    while fed < 60:
        g = party(d)[idx]
        if g.friendship >= goal:
            log.info("friendship %d >= %d after %d bottles (%d wasted)",
                     g.friendship, goal, fed, wasted)
            return True
        bag = d.state.bag().get("items") or {}
        pick = next((v for v in VITAMINS
                     if v not in dead and bag.get(v, 0) > 0), None)
        if pick is None:
            log.info("no usable bottle left at friendship %d (retired %s, "
                     "bag %s)", g.friendship, sorted(dead),
                     {k: n for k, n in bag.items() if k in VITAMINS})
            return g.friendship >= goal
        stat = BOTTLE_EV[pick]
        f0, ev0, n0 = g.friendship, g.evs.get(stat, 0), bag.get(pick, 0)
        t.use_on_mon(pick, who)
        d.advance_scene(20_000)
        g = party(d)[idx]
        n1 = (d.state.bag().get("items") or {}).get(pick, 0)
        fed += 1
        log.info("  %-8s friendship %3d -> %-3d %-10s %3d -> %-3d bag %d -> %d",
                 pick, f0, g.friendship, stat, ev0, g.evs.get(stat, 0), n0, n1)
        if g.evs.get(stat, 0) > ev0:
            continue
        if n1 < n0:
            wasted += 1
            log.info("    consumed but %s did not change -- the picker landed "
                     "on another slot; retrying", who)
            if wasted > 8:
                log.info("    too many mis-picks; giving up on bottling")
                return False
            continue
        dead.add(pick)
        log.info("    retiring %s (%s=%d, total=%d)", pick, stat,
                 g.evs.get(stat, 0), sum(g.evs.values()))
    return party(d)[idx].friendship >= goal


# ---- phases --------------------------------------------------------------

def unseal(state, a, d):
    """A Driver on a save that can reach open ground, fighting out if it must.

    An Elite Four member's room shuts its entry door at runtime and only
    opens the exit on victory, so a leg that inherits
    `EverGrandeCity_PhoebesRoom` -- which is exactly where an interrupted lap
    leaves this save -- can neither Fly (indoors) nor walk out, and every
    later step reports "no Centre reachable". `elite_four.py` fights from
    wherever it stands, so it is the door. Measured twice on this save,
    from PhoebesRoom and from GlaciasRoom.

    Returns a NEW Driver when it had to fight, because the gauntlet is a
    subprocess that owns the file while it runs.
    """
    if surface(d):
        return d
    log.info("sealed in %s -- fighting out", d.map_name())
    d.save(state)
    del d
    gauntlet(state, f"{Path(state).stem}-g", a.lap_minutes)
    d = Driver(state)
    d.advance_scene(40_000)
    sg.unwedge(d)
    log.info("after the escape gauntlet: %s %s", d.map_name(), d.pos())
    return d


def phase_shedinja(state, a) -> bool:
    """NINCADA -> L20 with FIVE in the party, so the sixth slot is free."""
    d = Driver(state)
    d.advance_scene(40_000)
    sg.unwedge(d)
    log.info("=== SHEDINJA === start %s %s", d.map_name(), d.pos())
    d = unseal(state, a, d)
    if slot_of(d, "NINCADA") is None and boxed_find(d, "NINCADA")[0] is None:
        if not hunt_nincada(d, minutes=a.nincada_minutes):
            log.info("no NINCADA caught")
            d.save(state)
            return False
        d.save(state)
        log.info("NINCADA banked to %s", state)

    if not to_center(d):
        log.info("no Centre reachable from %s", d.map_name())
        d.save(state)
        return False
    st, t = Storage(d), Teacher(d)
    if not free_the_share(d, st, t, target="NINCADA"):
        d.save(state)
        return False
    if not pair_up(d, st, "NINCADA"):
        d.save(state)
        return False
    n = mon_of(d, "NINCADA")
    log.info("NINCADA L%s exp=%s", n.level, n.experience)
    if not give_share(d, t, "NINCADA"):
        d.save(state)
        return False
    if not bench_behind_escorts(d, st, "NINCADA", size=5):
        d.save(state)
        return False
    try:
        d.heal_at_nearest_center()
    except Exception:  # noqa: BLE001
        pass
    park_in_hall(d)
    d.save(state)
    del d

    feed = f"{Path(state).stem}-g"
    for lap in range(a.laps):
        rc = gauntlet(state, feed, a.lap_minutes)
        d = Driver(state)
        d.advance_scene(40_000)
        got = SHEDINJA_DEX in caught_set(d)
        nin = mon_of(d, "NINCADA")
        log.info("lap %d rc=%s: SHEDINJA=%s NINCADA=%s", lap + 1, rc, got,
                 f"L{nin.level} exp={nin.experience}" if nin else "evolved")
        show(d, "after lap")
        if got:
            d.save(state)
            del d
            return True
        if nin is None:
            # It evolved with no SHEDINJA -- the party was full. Nothing here
            # can undo that; report it rather than looping.
            log.info("NINCADA evolved but no SHEDINJA flag: party was full")
            d.save(state)
            del d
            return False
        del d
    return False


def staged(d, who) -> bool:
    """`who` is in the party AND holding the share: nothing to stage."""
    m = mon_of(d, who)
    return m is not None and m.held_item == SHARE_ID


def phase_crobat(state, a) -> bool:
    d = Driver(state)
    d.advance_scene(40_000)
    sg.unwedge(d)
    log.info("=== CROBAT === start %s %s", d.map_name(), d.pos())
    d = unseal(state, a, d)

    # STAGING NEEDS A CENTRE; A LEVEL-UP DOES NOT. On a re-run the share is
    # already on GOLBAT, so skip the PC entirely rather than spending four
    # minutes of menus to arrive at the same party.
    if not staged(d, "GOLBAT"):
        if not to_center(d):
            log.info("no Centre reachable from %s", d.map_name())
            d.save(state)
            return False
        st, t = Storage(d), Teacher(d)
        if not free_the_share(d, st, t, target="GOLBAT"):
            d.save(state)
            return False
        if not pair_up(d, st, "GOLBAT"):
            d.save(state)
            return False
        if not give_share(d, t, "GOLBAT"):
            d.save(state)
            return False
    else:
        t = Teacher(d)
        log.info("GOLBAT is already staged with the share")
    g = mon_of(d, "GOLBAT")
    f_start = g.friendship
    log.info("GOLBAT L%s exp=%s friendship=%d evs=%s met_location=%s",
             g.level, g.experience, f_start, g.evs, g.met_location)
    # ONLY BOTTLE IF THE FRIENDSHIP IS NOT THERE YET, and ask the mon rather
    # than the bag. `use_on_mon` empties the whole STACK (see `bottle`), so a
    # re-run of this leg finds a friendship of 255 and a vitamin pocket of
    # zero -- and buying sixty more costs 588,000 against the 411,999 left,
    # which would fail the purchase and abort a phase that had nothing left to
    # do.
    if g.friendship < a.goal:
        if not buy_vitamins(d, each=a.bottles):
            d.save(state)
            return False
        d.save(state)
        if not to_cave(d):
            d.save(state)
            return False
        if not bottle(d, t, "GOLBAT", goal=a.goal):
            gg = mon_of(d, "GOLBAT")
            log.info("bottling fell short at friendship %s",
                     gg.friendship if gg else "?")
            d.save(state)
            return False
        g = mon_of(d, "GOLBAT")
        log.info("GOLBAT friendship %d -> %d, evs=%s (total %d)", f_start,
                 g.friendship, g.evs, sum(g.evs.values()))
        d.save(state)
    else:
        log.info("GOLBAT is already at friendship %d >= %d -- straight to the "
                 "level-up", g.friendship, a.goal)

    def evolved():
        return slot_of(d, "GOLBAT") is None

    # THE LEVEL-UP COMES FROM WILD BATTLES, NOT THE LEAGUE.
    #
    # The League is where the NINCADA had to go, because 12,800 exp is one
    # gauntlet pass and nothing less. GOLBAT needs 3,781 (L35 exp 42875 =
    # 35^3, MEDIUM_FAST), which is about nine knockouts of Victory Road's
    # L36-40 land table at `expYield * level / 7 / 2` a share -- and a wild
    # map cannot take the holder away from it.
    #
    # MEASURED, in the League, on this save: `_forced_switch` picks the party
    # member that best RESISTS the incoming move (battle.py:2254-2279), and
    # `--protect-bench` cannot veto that -- it only vetoes VOLUNTARY switches
    # (elite_four.py:118-150). One misread party menu ("stale party menu ...
    # treating it as a forced replacement") fronted the L35 GOLBAT against
    # Sidney's CACTURNE at the FIRST knockout and it fainted five turns later
    # (`T7 attack:1 WING ATTACK#1 | me 46->0`), which by
    # battle_script_commands.c:3361-3364 ends its earning for the whole lap.
    # A fainted holder also pays FRIENDSHIP_EVENT_FAINT_LARGE, -10 at this
    # tier, so the friendship this leg spent 588,000 on is what the retry
    # risks. Behind a level-100 sweeping L38 wilds there is no forced
    # replacement at all: nothing faints.
    if a.wild_minutes > 0 and level_on_wilds(d, "GOLBAT", a.wild_minutes):
        d.save(state)
        log.info("GOLBAT evolved on %s", d.map_name())
        return CROBAT_DEX in caught_set(d)
    d.save(state)
    if a.cave_minutes > 0 and not evolved():
        pace(d, sweep_policy, evolved, minutes=a.cave_minutes, terrain="cave")
        d.save(state)
    if evolved():
        return CROBAT_DEX in caught_set(d)
    log.info("no level from wilds -- taking GOLBAT to the League")
    park_in_hall(d)
    d.save(state)
    del d
    feed = f"{Path(state).stem}-g"
    for lap in range(max(1, a.laps)):
        gauntlet(state, feed, a.lap_minutes)
        d = Driver(state)
        d.advance_scene(40_000)
        got = CROBAT_DEX in caught_set(d)
        gg = mon_of(d, "GOLBAT")
        log.info("lap %d: CROBAT=%s GOLBAT=%s", lap + 1, got,
                 f"L{gg.level} friendship={gg.friendship}" if gg else "evolved")
        d.save(state)
        if got:
            del d
            return True
        del d
    return False


def report(state) -> dict:
    d = Driver(state)
    d.advance_scene(40_000)
    caught = caught_set(d)
    g = None
    for slot, mo in fresh_dex(d).boxed():
        if d.names.species(mo.species).upper() in ("GOLBAT", "CROBAT"):
            g = (d.names.species(mo.species).upper(), mo.friendship)
    for m in party(d):
        if spn(d, m) in ("GOLBAT", "CROBAT"):
            g = (spn(d, m), m.friendship)
    out = {"dex": len(caught), "CROBAT": CROBAT_DEX in caught,
           "SHEDINJA": SHEDINJA_DEX in caught, "golbat": g,
           "map": d.map_name(), "party": [(spn(d, m), m.level, m.friendship)
                                          for m in party(d)]}
    log.info("COLD READ %s", out)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True)
    ap.add_argument("--only", default="both",
                    choices=("both", "shedinja", "crobat", "report"))
    ap.add_argument("--nincada-minutes", type=float, default=20.0)
    ap.add_argument("--cave-minutes", type=float, default=0.0,
                    help="opt-in: pace the cave for the level-up instead of "
                         "the League. Its encounter rate is 4; a 15-minute "
                         "pass produced no battles at all.")
    ap.add_argument("--wild-minutes", type=float, default=30.0,
                    help="pace Victory Road to level the share holder; this "
                         "is where GOLBAT's evolving level-up comes from.")
    ap.add_argument("--lap-minutes", type=float, default=14.0)
    ap.add_argument("--laps", type=int, default=3)
    ap.add_argument("--bottles", type=int, default=10)
    ap.add_argument("--goal", type=int, default=FRIENDSHIP_GOAL)
    a = ap.parse_args(argv)
    state = a.state

    if a.only == "report":
        report(state)
        return 0

    d = Driver(state)
    d.advance_scene(40_000)
    caught = caught_set(d)
    log.info("start: dex %d, CROBAT=%s SHEDINJA=%s, at %s", len(caught),
             CROBAT_DEX in caught, SHEDINJA_DEX in caught, d.map_name())
    want_shed = SHEDINJA_DEX not in caught and a.only in ("both", "shedinja")
    want_crob = CROBAT_DEX not in caught and a.only in ("both", "crobat")
    del d
    if not (want_shed or want_crob):
        log.info("nothing to do")
        return 0

    landed = []
    if want_shed and phase_shedinja(state, a):
        landed.append("SHEDINJA")
    if want_crob and phase_crobat(state, a):
        landed.append("CROBAT")
    out = report(state)
    log.info("RESULT landed=%s dex=%s", landed, out["dex"])
    return 0 if (landed or not (want_shed or want_crob)) else 1


if __name__ == "__main__":
    raise SystemExit(main())
