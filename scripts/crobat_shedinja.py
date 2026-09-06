#!/usr/bin/env python
"""CROBAT and SHEDINJA: the two evolutions the by-level grinder cannot reach.

Both are dex entries that no amount of experience alone produces, and they fail
for different reasons.

SHEDINJA is not an evolution at all -- it is a SIDE EFFECT of one. When a mon
whose first evolution method is `EVO_LEVEL_NINJASK` evolves, the scene calls
`CreateShedinja` (pret/src/evolution_scene.c:493-527), which copies the mon
into `gPlayerParty[gPlayerPartyCount]` and overwrites its species with the
SECOND row of the evolution table
(`[SPECIES_NINCADA] = {{EVO_LEVEL_NINJASK, 20, SPECIES_NINJASK},
                       {EVO_LEVEL_SHEDINJA, 20, SPECIES_SHEDINJA}}`,
pret/src/data/pokemon/evolution.h:144-145) and then sets the SEEN and CAUGHT
dex flags itself (`:519-520`). The one and only precondition is on line 497:

    if (gEvolutionTable[preEvoSpecies][0].method == EVO_LEVEL_NINJASK
        && gPlayerPartyCount < 6)

So Gen 3 asks for A FREE PARTY SLOT AND NOTHING ELSE. There is no Poke Ball
check anywhere in `CreateShedinja` -- that requirement arrives in Gen 4 -- and
this matters practically: a bag with no balls does not cost the entry, but a
party of six silently does, with no message of any kind. The party is kept at
three here, so the slot is never in question.

CROBAT is a friendship evolution, and friendship is where the real work is.
`GetEvolutionTargetSpecies` wants `friendship >= 220` at a LEVEL-UP
(pret/src/pokemon_3.c:291-299), a boxed GOLBAT sits at the wild-caught base of
70, and the deltas are tiered (pokemon_3.c:649-662):

    { 5,  3,   2}, // FRIENDSHIP_EVENT_GROW_LEVEL
    { 5,  3,   2}, // FRIENDSHIP_EVENT_VITAMIN
    { 1,  1,   1}, // FRIENDSHIP_EVENT_WALKING     (and only on a coin flip,
                                                    `!(Random() & 1)`, :691)

Levels are the obvious lever and the wrong one: 70 -> 220 across those tiers is
FORTY-NINE level-ups, which is a bigger grind than the whole rest of this dex.
Walking is worse -- 150 points is ~38,400 steps at one point per 256, halved
again by the coin flip. Vitamins pay the same 5/3/2 as a level-up and cost only
money, and this save has 999,999 of it against the Energy Guru's 9,800 a bottle
(pret/data/maps/SlateportCity/scripts.inc:52-64 sells all six).

Their limit is EVs, not cash: `PokemonUseItemEffects` bails out with no
friendship at all once `GetMonEVCount(pkmn) >= 510`
(pret/src/pokemon_item_effect.c:250-252) or the individual stat is at 100
(`:254`), and the friendship branch is gated on `retVal == 0` (`:471`) -- i.e.
on the EV actually having been applied. Fifty-one bottles is therefore the hard
ceiling for one mon, and 70 -> 220 needs about forty-eight of them. That is why
this feeds ONE bottle at a time and reads `mon.friendship` back after each: the
ceiling is close enough that guessing would be indistinguishable from failure.

The last +2 is left to the level-up itself, because the evolution CHECK is what
consumes it: `AdjustFriendship(FRIENDSHIP_EVENT_GROW_LEVEL)` runs at
battle_script_commands.c:3530 before the post-battle evolution pass, so a mon
brought to 218 by bottles crosses 220 on the same level-up that evolves it.
That level-up comes from the EXP. SHARE at the Elite Four, which is the same
engine that takes the NINCADA to 20.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from pokeagent.trek import Driver, TravelInterrupted  # noqa: E402
from pokeagent.dex import DexTarget  # noqa: E402
from pokeagent.storage import Storage  # noqa: E402
from pokeagent.teaching import Teacher  # noqa: E402
from pokeagent.mart import Mart  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("cs")

SHARE_ID = 182
SHARE = "EXP SHARE"          # no period: "EXP. SHARE" resolves to None here
LEAD = "PELIPPER"            # the L100 that sweeps
ESCORT = "BLAZIKEN"          # the second L100, so the holder is never fronted
VITAMINS = ("HP UP", "PROTEIN", "IRON", "CARBOS", "CALCIUM", "ZINC")
GURU = (5, 47)               # SlateportCity_EventScript_EnergyGuru, map.json
#: Stop bottling here. The level-up that evolves adds its own +2 first
#: (battle_script_commands.c:3530 runs before the evolution pass), so 218 is
#: 220 by the time `GetEvolutionTargetSpecies` looks.
FRIENDSHIP_GOAL = 218


# ---- small helpers ---------------------------------------------------------

def party(d):
    return [m for m in d.state.party() if not m.is_egg]


def spn(d, m):
    return d.names.species(m.species).upper()


def show(d, label=""):
    log.info("party%s: %s", f" {label}" if label else "",
             [(m.nickname, spn(d, m), m.level, m.friendship, m.held_item)
              for m in party(d)])


def slot_of(d, species=None, nick=None):
    for i, m in enumerate(party(d)):
        if species and spn(d, m) == species:
            return i
        if nick and (m.nickname or "").upper() == nick:
            return i
    return None


def mon_of(d, species=None, nick=None):
    i = slot_of(d, species=species, nick=nick)
    return party(d)[i] if i is not None else None


def holder(d):
    for i, m in enumerate(party(d)):
        if m.held_item == SHARE_ID:
            return i
    return None


def fresh_dex(d):
    return DexTarget(d.emu, d.names, d.consts, d.nav, spec=d.spec)


def boxed_find(d, species):
    """`(box, slot, mon)` for a boxed species, re-read every time.

    Deposits fill the first free slot, so any index cached across one is a
    different mon by the time it is used.
    """
    for flat, mon in fresh_dex(d).boxed():
        if d.names.species(mon.species).upper() == species:
            return flat // 30, flat % 30, mon
    return None, None, None


def unwedge(d) -> bool:
    """Clear whatever owns input before anything tries to walk."""
    if d.at_title():
        log.info("on the title screen -- taking CONTINUE")
        if not d.resume_from_title():
            return False
    d.advance_scene(40_000)
    for _ in range(10):
        if not d.scene_active():
            return True
        d.close_menus()
        d.settle(400)
        d.advance_scene(20_000)
    return not d.scene_active()


#: Towns whose Centre this run is happy to land in. MAUVILLE first because it
#: is the most connected landing on the board.
CENTRE_TOWNS = ("MauvilleCity", "SlateportCity", "LilycoveCity",
                "RustboroCity", "SootopolisCity")


def to_center(d) -> bool:
    """Stand in a Pokemon Center, flying out of the wilderness if need be.

    `heal_at_nearest_center` only walks: started on Route 119 -- where the
    canonical save now sits -- it answers False and every storage phase dies
    with "no Pokemon Center reachable". A Fly landing puts us on a town tile
    whose Centre IS walkable.
    """
    unwedge(d)
    if d.map_name().endswith("PokemonCenter_1F"):
        return True
    try:
        d.heal_at_nearest_center()
    except Exception as exc:  # noqa: BLE001
        log.info("heal_at_nearest_center: %s", str(exc)[:90])
    if d.map_name().endswith("PokemonCenter_1F"):
        return True
    for town in CENTRE_TOWNS:
        try:
            if not d.fly_to(town):
                continue
            unwedge(d)
            d.heal_at_nearest_center()
        except Exception as exc:  # noqa: BLE001
            log.info("fly/heal via %s: %s", town, str(exc)[:90])
        if d.map_name().endswith("PokemonCenter_1F"):
            return True
    return False


def shrink_to(d, st, keep, floor=1) -> None:
    """Deposit party members until only `keep` (a set of species) is left."""
    for _ in range(8):
        p = party(d)
        if len(p) <= floor:
            return
        drop = next((i for i, m in enumerate(p) if spn(d, m) not in keep), None)
        if drop is None:
            return
        if not st.deposit(drop):
            log.info("deposit refused: %s", getattr(st, "last_reason", "?"))
            return


def ensure_party(d, st, species) -> bool:
    """Withdraw `species` if it is not already in the party."""
    if slot_of(d, species=species) is not None:
        return True
    box, slot, mon = boxed_find(d, species)
    if mon is None:
        log.info("%s is in neither party nor boxes", species)
        return False
    if not st.withdraw(box, slot):
        log.info("withdraw %s refused: %s", species,
                 getattr(st, "last_reason", "?"))
        return False
    return slot_of(d, species=species) is not None


# ---- phases ---------------------------------------------------------------

def phase_stage(d, st, t) -> bool:
    """Free the EXP. SHARE and reduce the party to the lead plus GOLBAT."""
    if not to_center(d):
        log.info("no Pokemon Center reachable from %s", d.map_name())
        return False
    show(d, "on arrival")

    # The share is welded to whoever wears it (a deposited mon takes its item
    # out of the bag with it), and the party picker's geometry is only
    # trustworthy on a short party -- so shrink first, then unequip.
    h = holder(d)
    if h is not None:
        keep = {spn(d, party(d)[h]), LEAD}
        shrink_to(d, st, keep, floor=2)
        h = holder(d)
        if h is not None and not t.take_from_mon(h):
            log.info("could not unequip the share: %s",
                     getattr(t, "last_reason", "?"))
            return False
        log.info("share is in the bag")
    shrink_to(d, st, {LEAD}, floor=1)
    if not ensure_party(d, st, "GOLBAT"):
        return False
    g = mon_of(d, species="GOLBAT")
    log.info("GOLBAT L%s friendship=%s evs=%s (total %d) met_location=%s "
             "ball=%s", g.level, g.friendship, g.evs, sum(g.evs.values()),
             g.met_location, getattr(g, "poke_ball", "?"))
    show(d, "staged")
    return True


#: Which EV each bottle raises. Needed because the per-stat ceiling is what
#: retires a flavour: `PokemonUseItemEffects` only applies (and only then pays
#: friendship) while that stat is under 100 (pokemon_item_effect.c:254).
BOTTLE_EV = {"HP UP": "hp", "PROTEIN": "attack", "IRON": "defense",
             "CARBOS": "speed", "CALCIUM": "sp_attack", "ZINC": "sp_defense"}


def phase_buy(d, buy_each=10) -> bool:
    """Buy ten of each bottle from Slateport's Energy Guru.

    Ten is the per-stat ceiling, so ten of each is every bottle this mon can
    ever legally take -- 60 bought against a 510-total cap that allows 51.
    """
    have = d.state.bag().get("items") or {}
    if all(have.get(v, 0) >= buy_each for v in VITAMINS):
        log.info("bottles already in the bag: %s",
                 {v: have.get(v) for v in VITAMINS})
        return True
    if d.map_name() != "SlateportCity":
        if not d.fly_to("SlateportCity"):
            log.info("could not fly to Slateport")
            return False
    unwedge(d)
    gx, gy = GURU
    if not (d.goto(gx, gy + 1) or d.goto(gx - 1, gy) or d.goto(gx + 1, gy)):
        log.info("could not reach the Energy Guru at %s: %s", GURU,
                 getattr(d, "last_goto_reason", "?"))
        return False
    d.talk_to(gx, gy)
    d.settle(600)
    mart = Mart(d)
    for _ in range(10):
        if mart.is_open():
            break
        d.emu.run_sequence("A:6 .:60")
        d.settle(400)
    if not mart.is_open():
        log.info("the Energy Guru's shop never opened")
        return False
    log.info("stock: %s", [r["name"] for r in mart.items()])
    for v in VITAMINS:
        short = buy_each - (d.state.bag().get("items") or {}).get(v, 0)
        if short > 0 and not mart.buy(v, short):
            log.info("buying %s failed: %s", v, mart.last_reason)
    mart.leave()
    d.settle(600)
    unwedge(d)
    log.info("bag items now: %s", d.state.bag().get("items"))
    return all((d.state.bag().get("items") or {}).get(v, 0) > 0
               for v in VITAMINS)


def phase_cave(d) -> bool:
    """Stand inside the Cave of Origin, which is where GOLBAT was MET.

    Worth the trip: every positive friendship event pays ONE MORE POINT when
    the mon's met location matches the map you are standing on --
    `if (GetMonData(mon, MON_DATA_MET_LOCATION) == sav1_map_get_name())
     friendship++` (pret/src/pokemon_3.c:707-708), and the same clause is in
    the item path (pokemon_item_effect.c:484-486). This GOLBAT reads
    met_location=72 = MAPSEC_CAVE_OF_ORIGIN
    (include/constants/region_map_sections.h:81), so bottling in here turns
    5/3/2 into 6/4/3 -- 36 bottles instead of 48, against a hard ceiling of
    51. It also pays the level-up that evolves.
    """
    if d.map_name().startswith("CaveOfOrigin"):
        return True
    if not d.map_name().startswith("Sootopolis"):
        if not d.fly_to("SootopolisCity"):
            log.info("could not fly to Sootopolis")
            return False
    unwedge(d)
    try:
        d.travel("CaveOfOrigin_Entrance", on_battle="fight", budget_s=240)
    except TravelInterrupted:
        d.fight()
    except Exception as exc:  # noqa: BLE001
        log.info("travel CaveOfOrigin_Entrance: %s", str(exc)[:90])
    if not d.map_name().startswith("CaveOfOrigin"):
        # The stairs door is a plain warp at (31,16); standing on one does not
        # fire it, so ask for the warp explicitly.
        try:
            d.take_warp(31, 16)
        except Exception as exc:  # noqa: BLE001
            log.info("take_warp(31,16): %s", str(exc)[:90])
    log.info("now on %s %s", d.map_name(), d.pos())
    return d.map_name().startswith("CaveOfOrigin")


def phase_bottle(d, t, goal=FRIENDSHIP_GOAL, who="GOLBAT") -> bool:
    """Feed bottles one at a time, reading the mon back after every one.

    Three outcomes have to be told apart, and only the mon's own fields can
    do it:

    * the EV moved     -> it landed, and friendship moved with it (the
                          friendship branch is gated on the EV having applied,
                          pokemon_item_effect.c:471);
    * no EV, no bottle -> refused; if that stat is at 100 the flavour is
                          finished, otherwise retry;
    * no EV, bottle    -> `_pick_party_member` landed on somebody else
      gone        (teaching.py:598-605). Retry the same flavour.
    """
    idx = slot_of(d, species=who)
    if idx is None:
        log.info("%s is not in the party", who)
        return False
    log.info("bottling on %s (met bonus %s)", d.map_name(),
             "ACTIVE" if d.map_name().startswith("CaveOfOrigin") else "no")
    dead, fed, wasted = set(), 0, 0
    while fed < 70:
        g = party(d)[idx]
        if g.friendship >= goal:
            log.info("friendship %d >= %d after %d bottles (%d wasted)",
                     g.friendship, goal, fed, wasted)
            return True
        bag = d.state.bag().get("items") or {}
        pick = next((v for v in VITAMINS
                     if v not in dead and bag.get(v, 0) > 0), None)
        if pick is None:
            log.info("no usable bottle left at friendship %d (retired: %s, "
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
        log.info("  %-8s friendship %3d -> %-3d %s %3d -> %-3d bag %d -> %d",
                 pick, f0, g.friendship, stat, ev0, g.evs.get(stat, 0), n0, n1)
        if g.evs.get(stat, 0) > ev0:
            continue
        if n1 < n0:
            wasted += 1
            log.info("    the bottle was consumed but %s did not change -- it "
                     "landed on another slot; retrying", who)
            if wasted > 8:
                log.info("    too many mis-picks; giving up on bottling")
                return False
            continue
        if g.evs.get(stat, 0) >= 100 or sum(g.evs.values()) >= 510:
            dead.add(pick)
            log.info("    %s is finished on this mon (%s=%d, total=%d)", pick,
                     stat, g.evs.get(stat, 0), sum(g.evs.values()))
            continue
        dead.add(pick)
        log.info("    %s was refused with %s=%d -- retiring it anyway", pick,
                 stat, g.evs.get(stat, 0))
    return party(d)[idx].friendship >= goal


def hunt_nincada(d, minutes=25.0) -> bool:
    """Catch a NINCADA on Route 116 (land, 20% across two level slots).

    A custom policy, not `Catcher`: NINCADA's caught flag is already set (the
    party's NINJASK came from one), and the catcher declines anything already
    owned -- correctly, for its own job. This wants the MON, not the entry.
    """
    if slot_of(d, species="NINCADA") is not None:
        return True
    if d.map_name() != "Route116":
        if not d.fly_to("RustboroCity"):
            log.info("could not fly to Rustboro")
            return False
        unwedge(d)
        try:
            d.travel("Route116", on_battle="fight", budget_s=240)
        except TravelInterrupted:
            d.fight()
        except Exception as exc:  # noqa: BLE001
            log.info("travel Route116: %s", str(exc)[:90])
    if d.map_name() != "Route116":
        log.info("not on Route116 (on %s)", d.map_name())
        return False

    balls = (d.state.bag().get("poke_balls") or {})
    ball = "ULTRA BALL" if balls.get("ULTRA BALL") else next(iter(balls), None)
    log.info("hunting NINCADA with %s (%s)", ball, balls)
    if ball is None:
        log.info("no balls in the bag")
        return False

    def enemy_now():
        """The wild mon, read from `gEnemyParty` rather than the frame.

        `battle.frame()["enemy"]` is `gBattleMons[1]`, which is STALE right
        after `battle_ready()` -- it can still describe the PREVIOUS battle's
        mon, so a decision made off the first frame flees the species it was
        sent to catch (reported by RodHunt on this same harness). The party
        array is written when the encounter is generated, so it is right from
        the first frame.
        """
        try:
            foes = d.state.enemy_party()
        except Exception:  # noqa: BLE001
            return ""
        if not foes:
            return ""
        return d.names.species(foes[0].species).upper()

    def policy(frame):
        name = enemy_now()
        if name == "NINCADA":
            return ("ball", ball)
        if name:
            return "flee"
        # Nothing readable yet: attack rather than throw a ball blind.
        return sweep_policy(frame)

    d.battle_policy = policy
    return pace(d, policy, lambda: slot_of(d, species="NINCADA") is not None,
                minutes=minutes)


def sweep_policy(frame):
    """Hit with the strongest move that has PP, and never switch.

    `tactics.recommend` ranks "switch to something that resists this" above
    damage, which is right in general and catastrophic with an EXP. SHARE on
    the bench: the holder gets fronted and knocked out, and a fainted mon is
    skipped by both the participant count and the exp loop
    (battle_script_commands.c:3361-3364, :3436). Same reasoning as
    elite_four.protect_bench_policy, minus the gauntlet.
    """
    best, score = 0, -1.0
    for i, mv in enumerate((frame or {}).get("moves") or []):
        if not mv or not mv.get("pp"):
            continue
        s = (mv.get("power") or 0) * (mv.get("effect_mult") or 1.0)
        if s > score:
            best, score = i, s
    return ("attack", best)


def phase_level(d, who="GOLBAT", minutes=45.0) -> bool:
    """Pace the cave until the share holder levels up -- which evolves it.

    The holder never enters the ring: the share pays it half of every
    knockout the lead makes, and that payout still sets `gLeveledUpInBattle`,
    so the post-battle evolution pass picks it up (teaching.py:706-713).
    """
    idx = slot_of(d, species=who)
    if idx is None:
        log.info("%s is not in the party", who)
        return False
    start = party(d)[idx]
    log.info("levelling %s L%s exp=%s friendship=%s (holding %s) on %s",
             who, start.level, start.experience, start.friendship,
             start.held_item, d.map_name())

    def evolved():
        p = party(d)
        return not any(spn(d, m) == who for m in p)

    d.battle_policy = sweep_policy
    pace(d, sweep_policy, evolved, minutes=minutes, terrain="cave")
    show(d, "after levelling")
    return evolved()


def pace(d, policy, stop, minutes=25.0, terrain="grass") -> bool:
    """Walk encounter terrain until `stop()`, fighting with `policy`.

    `goto` is the walker rather than hand-stepping: `step_dir` returns False
    for free while a scene owns input, and a hand-stepped loop spun 7.5
    million times in 150 seconds without moving (collect.py:471-479).
    """
    cells = set(d.nav.find_tiles(d.map_name(), terrain))
    if terrain == "cave" and not cells:
        # Cave floors are ordinary walkable ground rather than a terrain kind,
        # so pace the reachable floor itself.
        cells = set(d.nav.reachable(d.map_name(), d.pos(), d.elevation()))
    reach = set(d.nav.reachable(d.map_name(), d.pos(), d.elevation()))
    px, py = d.pos()
    spots = sorted(cells & reach, key=lambda c: abs(c[0] - px) + abs(c[1] - py))
    if not spots:
        log.info("no reachable %s on %s (%d cells on the map)", terrain,
                 d.map_name(), len(cells))
        return False
    log.info("%d reachable %s cells, nearest %s", len(spots), terrain,
             spots[0])
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
            if d.goto(*target, on_battle="raise"):
                stalled = 0
            else:
                stalled += 1
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
    # CLEAR IT. `_journey_deadline` is per-DRIVER, not per-call, so a deadline
    # left behind from the last walk makes every later `take_warp` refuse its
    # approach cell (confirmed on this harness today).
    d._journey_deadline = None
    log.info("paced %s: %d battles, stop=%s", d.map_name(), fights, stop())
    return stop()


def phase_catch(d, st, t) -> bool:
    """Party -> [LEAD, ESCORT, NINCADA] with the share on the NINCADA.

    Order is deliberate: the escorts are withdrawn BEFORE the catch so the
    NINCADA lands in the LAST slot. A forced switch reaches the last slot only
    after everything in front of it has fainted, and a fainted holder collects
    nothing for the rest of the lap (battle_script_commands.c:3361-3364).
    """
    if not to_center(d):
        log.info("no Center to stage from (%s)", d.map_name())
        return False
    shrink_to(d, st, {LEAD}, floor=1)
    if not ensure_party(d, st, LEAD):
        return False
    if not ensure_party(d, st, ESCORT):
        log.info("no %s to escort with -- going on with the lead alone",
                 ESCORT)
    show(d, "before the hunt")
    if not hunt_nincada(d):
        return False
    if not to_center(d):
        log.info("caught the NINCADA but could not reach a Center")
    d.heal()
    show(d, "after the hunt")

    idx = slot_of(d, species="NINCADA")
    if not t.give_from_field(idx, SHARE):
        log.info("field give failed (%s) -- retrying on a two-mon party",
                 getattr(t, "last_reason", "?"))
        h = holder(d)
        if h is not None:
            t.take_from_mon(h)
        shrink_to(d, st, {LEAD, "NINCADA"}, floor=2)
        idx = slot_of(d, species="NINCADA")
        if not (t.give_from_field(idx, SHARE)
                or t.give_to_mon(SHARE, party(d)[idx].nickname)):
            log.info("could not hand the share to the NINCADA")
            return False
        ensure_party(d, st, ESCORT)
    h = holder(d)
    if h is None or spn(d, party(d)[h]) != "NINCADA":
        log.info("the share is on %s, not the NINCADA -- refusing a lap that "
                 "pays nobody",
                 spn(d, party(d)[h]) if h is not None else "nobody")
        return False
    log.info("share is on slot %d (%s)", h, spn(d, party(d)[h]))
    if len(party(d)) >= 6:
        log.info("party is FULL -- CreateShedinja needs gPlayerPartyCount < 6 "
                 "(evolution_scene.c:497); depositing something")
        shrink_to(d, st, {LEAD, ESCORT, "NINCADA"}, floor=3)
    show(d, "ready for the gauntlet")
    return True


def phase_golbat_share(d, st, t) -> bool:
    """Hand the share to the bottled GOLBAT for the level-up that evolves it."""
    if not to_center(d):
        log.info("no Center to stage from (%s)", d.map_name())
        return False
    d.heal()
    h = holder(d)
    if h is not None and spn(d, party(d)[h]) != "GOLBAT":
        if not t.take_from_mon(h):
            log.info("could not free the share: %s",
                     getattr(t, "last_reason", "?"))
            return False
    shrink_to(d, st, {LEAD}, floor=1)
    if not ensure_party(d, st, "GOLBAT"):
        return False
    idx = slot_of(d, species="GOLBAT")
    if not (t.give_from_field(idx, SHARE)
            or t.give_to_mon(SHARE, party(d)[idx].nickname)):
        log.info("could not hand the share to GOLBAT: %s",
                 getattr(t, "last_reason", "?"))
        return False
    h = holder(d)
    if h is None or spn(d, party(d)[h]) != "GOLBAT":
        log.info("share landed on %s, not GOLBAT",
                 spn(d, party(d)[h]) if h is not None else "nobody")
        return False
    ensure_party(d, st, ESCORT)
    show(d, "ready to level GOLBAT")
    return True


def park_in_hall(d) -> bool:
    """Get to EverGrandeCity_PokemonLeague, where elite_four.py starts."""
    unwedge(d)
    try:
        for _ in range(4):
            if d.flight.flyable_here():
                break
            d.flight.step_outside()
        if d.map_name() != "EverGrandeCity":
            d.fly_to("EverGrandeCity")
        import league_loop

        league_loop.into_hall(d)
    except Exception as exc:  # noqa: BLE001
        log.info("into_hall: %s", str(exc)[:100])
    log.info("parked at %s %s", d.map_name(), d.pos())
    return d.map_name() == "EverGrandeCity_PokemonLeague"


def phase_report(d) -> dict:
    t = fresh_dex(d)
    caught, seen = t.dex_flags(d.state)
    out = {}
    for e in t.achievable:
        nm = getattr(e, "name", "").upper()
        if nm in ("CROBAT", "SHEDINJA", "NINJASK", "GOLBAT", "NINCADA"):
            out[nm] = e.natdex in caught
    log.info("dex: %s", t.summary(d.state))
    log.info("caught=%d line=%s", len(caught), out)
    show(d, "now")
    for flat, m in t.boxed():
        nm = d.names.species(m.species).upper()
        if nm in ("GOLBAT", "CROBAT", "SHEDINJA", "NINJASK", "NINCADA"):
            log.info("boxed %d/%-2d %-9s L%-3s friendship=%s", flat // 30,
                     flat % 30, nm, t.boxed_level(m), m.friendship)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True)
    ap.add_argument("--out")
    ap.add_argument("--phases", default="report",
                    help="stage,buy,golbat,cave,bottle,level,catch,park,report")
    ap.add_argument("--minutes", type=float, default=45.0)
    ap.add_argument("--goal", type=int, default=FRIENDSHIP_GOAL)
    a = ap.parse_args()
    if "line3" in a.state:
        log.info("refusing to touch the canonical line")
        return 2
    out = a.out or a.state
    phases = [p.strip() for p in a.phases.split(",") if p.strip()]

    d = Driver(a.state)
    unwedge(d)
    log.info("START %s %s money=%s", d.map_name(), d.pos(), d.state.money())
    st, t = Storage(d), Teacher(d)

    for name in phases:
        log.info("=== phase %s ===", name)
        if name == "stage":
            ok = phase_stage(d, st, t)
        elif name == "buy":
            ok = phase_buy(d)
        elif name == "cave":
            ok = phase_cave(d)
        elif name == "bottle":
            ok = phase_bottle(d, t, goal=a.goal)
        elif name == "level":
            ok = phase_level(d, minutes=a.minutes)
        elif name == "catch":
            ok = phase_catch(d, st, t)
        elif name == "golbat":
            ok = phase_golbat_share(d, st, t)
        elif name == "park":
            ok = park_in_hall(d)
        elif name == "report":
            phase_report(d)
            ok = True
        else:
            log.info("unknown phase %r", name)
            ok = False
        d.save(out)
        log.info("=== phase %s -> %s (saved %s) ===", name, ok, out)
        if not ok:
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
