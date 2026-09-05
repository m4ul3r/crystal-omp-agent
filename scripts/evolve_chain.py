#!/usr/bin/env python
"""Convert boxed pre-evolutions into dex entries, one Elite Four run each.

The boxes are full of species whose EVOLUTION is a missing dex entry, and
four of them are already PAST their level threshold -- they need one
in-battle level-up, nothing more, because `TryEvolvePokemon`
(pret/src/battle_main.c:5091-5113) runs off `gLeveledUpInBattle` and a
day-care level never sets it (pret/src/daycare.c:139-166).

The XP comes from the Elite Four with the EXP. SHARE on the target. A benched
holder is paid `calculatedExp / 2 / viaExpShare`
(pret/src/battle_script_commands.c:3375-3392) and that payout still sets
`gLeveledUpInBattle` (:3527) -- so the mon levels and evolves without ever
being sent out, which matters when it is a L5 SILCOON and the opponents are
L46-55.

Priority is fewest-levels-first, so the cheapest entries land even if the
clock runs out mid-list.
"""
import argparse
import logging
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from pokeagent.trek import Driver  # noqa: E402
from pokeagent.dex import DexTarget  # noqa: E402
from pokeagent.storage import Storage  # noqa: E402
from pokeagent.teaching import Teacher  # noqa: E402

log = logging.getLogger("evolve_chain")

BOX_SIZE = 30

#: (species, what it becomes) in fewest-levels-first order. Everything here
#: is a species the run already OWNS a copy of, boxed.
TARGETS = [
    ("MARILL", "Azumarill"),     # L25 held, evolves L18 -- past
    ("NATU", "Xatu"),            # L25 held, evolves L25 -- past
    ("GOLDEEN", "Seaking"),      # L34 held, evolves L33 -- past
    ("CHINCHOU", "Lanturn"),     # L29 held, evolves L27 -- past
    ("SURSKIT", "Masquerain"),   # L24 held, evolves L22 -- past
    ("ODDISH", "Gloom/Bellossom"),  # L27 held, evolves L21 -- past
    ("SILCOON", "Beautifly"),    # L5  -> L10
    ("ABRA", "Kadabra"),         # L10 -> L16
    ("HORSEA", "Seadra"),        # L30 -> L32
    ("BAGON", "Shelgon"),        # L25 -> L30
    ("DODUO", "Dodrio"),         # L25 -> L31
    ("RALTS", "Kirlia/Gardevoir"),  # L4 -> L20 -> L30
    ("MEDITITE", "Medicham"),    # L29 -> L37
    ("GEODUDE", "Graveler"),     # L8  -> L25
    ("SHROOMISH", "Breloom"),    # L5  -> L23
]


def dex_count(dex, state) -> int:
    """The registered dex number, parsed from the summary.

    NOT `owned_species`: an evolution SWAPS one species for another, so the
    owned count can stay flat while a dex entry is genuinely gained -- ODDISH
    becoming GLOOM loses one and gains one. The dex flags are the authority.
    """
    m = re.search(r"dex (\d+)/", dex.summary(state))
    return int(m.group(1)) if m else -1


def find_boxed(d, dex: DexTarget, species_name: str):
    """(flat_slot, level) of the lowest-level boxed copy, or None."""
    want = species_name.upper()
    best = None
    for slot, mon in dex.boxed():
        try:
            if d.names.species(mon.species).upper() != want:
                continue
            lv = dex.boxed_level(mon)
        except Exception:  # noqa: BLE001
            continue
        if best is None or lv < best[1]:
            best = (slot, lv)
    return best


def free_a_slot(d, storage, keep: set) -> bool:
    """Deposit the highest-level mon that is not the sweeper or a target."""
    party = [m for m in d.state.party() if not m.is_egg]
    if len(party) < 6:
        return True
    for i, m in sorted(enumerate(party), key=lambda p: -(p[1].level or 0)):
        nick = (m.nickname or "").upper()
        if nick in keep:
            continue
        log.info("  depositing %s L%s to free a slot", m.nickname, m.level)
        if storage.deposit(i):
            return True
        log.info("  deposit refused: %s", getattr(storage, "last_reason", "?"))
    return False


def to_pc(d) -> bool:
    """Stand somewhere with a PC, and prove it.

    EVERY target failed on this: the state sits at EverGrandeCity_Corridor5
    after a gauntlet, and `[storage] no PC tile on EverGrandeCity_Corridor5`
    refused all fourteen withdrawals in a row. The plateau's own Centre is on
    the LOWER tier and unreachable from up here, so `heal_at_nearest_center`
    flies to a town -- which is fine, the PC comes with the nurse.
    """
    if Storage(d).pc_cells():
        return True
    # FLY OUT EXPLICITLY. `heal_at_nearest_center` could not move us at all
    # from EverGrandeCity_Corridor5 -- the plateau's own Centre is on the
    # lower tier and there is no walkable route down -- so the batch came
    # back empty with `PC present: False` and every target was skipped.
    # Fly is refused indoors, so step out of the League first.
    try:
        # WALK OUT OF THE LEAGUE FIRST. Its rooms are a one-way corridor
        # chain, `step_outside` just moves to the NEXT indoor room
        # (Corridor5 -> SidneysRoom), fly is refused indoors, and nothing can
        # route back to the entrance -- so the batch came back empty twice.
        # `league_loop.leave_a_room` is the helper that knows the chain.
        if d.map_name().startswith("EverGrandeCity_") \
                and d.map_name() != "EverGrandeCity_PokemonCenter_1F":
            import league_loop

            for _ in range(10):
                if not d.map_name().startswith("EverGrandeCity_"):
                    break
                if not league_loop.leave_a_room(d):
                    break
            log.info("  walked out of the League to %s", d.map_name())
        if not d.flight.flyable_here():
            d.flight.step_outside()
        for town in ("LilycoveCity", "MauvilleCity", "FortreeCity",
                     "RustboroCity"):
            if d.fly_to(town):
                log.info("  flew to %s", d.map_name())
                break
    except Exception as exc:  # noqa: BLE001
        log.info("  fly out: %s", str(exc)[:90])
    # Then walk into that town's Centre, which is where the PC lives.
    for _ in range(2):
        if Storage(d).pc_cells():
            break
        try:
            d.heal_at_nearest_center()
        except Exception as exc:  # noqa: BLE001
            log.info("  centre: %s", str(exc)[:90])
    ok = bool(Storage(d).pc_cells())
    log.info("  at %s, PC present: %s", d.map_name(), ok)
    return ok


def swap_in_batch(d, dex, wanted, keep="SEA BIRD", limit=1):
    """Deposit everything but the sweeper, then withdraw up to `limit`
    targets -- ONE PC trip for a whole batch instead of one per species."""
    if not to_pc(d):
        return []
    storage = Storage(d)
    # KEEP THE FIGHTERS. Only ONE mon can hold the EXP. SHARE, so a second
    # target in the party earns nothing and costs a fighting slot -- and the
    # gauntlet is five real battles ending in Drake's dragons. Emptying the
    # bench left SEA BIRD alone with three L25 passengers: it beat Sidney,
    # Phoebe and Glacia, then fainted to Altaria and whited out, turning a
    # 70,000 exp run into one level.
    #
    # So deposit only what is needed to open ONE slot, lowest level first,
    # and never a mon strong enough to matter.
    for _ in range(6):
        party = [m for m in d.state.party() if not m.is_egg]
        if len(party) < 6:
            break
        victim = min(
            ((i, m) for i, m in enumerate(party)
             if (m.nickname or "").upper() != keep.upper()
             and d.names.species(m.species).upper() not in wanted),
            key=lambda p: (p[1].level or 0), default=(None, None))[0]
        if victim is None:
            break
        if not storage.deposit(victim):
            log.info("  deposit refused: %s",
                     getattr(storage, "last_reason", "?"))
            break
    # RESTOCK FIGHTERS if a previous pass stripped the party. The gauntlet
    # cannot be won by one bird.
    if len([m for m in d.state.party() if not m.is_egg]) < 5:
        strong = sorted(
            ((slot, mon) for slot, mon in dex.boxed()),
            key=lambda sm: -(dex.boxed_level(sm[1]) or 0))
        for slot, mon in strong:
            if len([m for m in d.state.party() if not m.is_egg]) >= 5:
                break
            lv = dex.boxed_level(mon) or 0
            if lv < 40:
                break
            if storage.withdraw(slot // BOX_SIZE, slot % BOX_SIZE):
                log.info("  recalled fighter %s L%d",
                         d.names.species(mon.species), lv)

    taken = []
    for species in wanted:
        if len([m for m in d.state.party() if not m.is_egg]) >= 6:
            break
        if any(d.names.species(m.species).upper() == species
               for m in d.state.party() if not m.is_egg):
            taken.append(species)
            continue
        found = find_boxed(d, dex, species)
        if found is None:
            continue
        slot, lv = found
        if storage.withdraw(slot // BOX_SIZE, slot % BOX_SIZE):
            log.info("  withdrew %s L%d", species, lv)
            taken.append(species)
        else:
            log.info("  withdraw %s refused: %s", species,
                     getattr(storage, "last_reason", "?"))
        if len(taken) >= limit:
            break
    storage.close()
    return taken


def give_share_to_target(d, species: str) -> str | None:
    """Hand the EXP. SHARE to a party member and report WHO actually got it.

    The party screen is not a list. RSE draws slot 0 as a tall box on the
    left and slots 1-5 as a two-column grid beside it, so DOWN is not "+1"
    and the picker lands next to the mon it was asked for -- verified twice:
    asking for NATU (index 1) put the share on GOLDEEN (index 2), with the
    item genuinely equipped while `give_to_mon` reported failure.

    Fixing that geometry properly needs the menu's own layout table. What the
    grind actually needs is only that SOME boxed pre-evolution is holding the
    share, so read the outcome instead of insisting on the input. Honest and
    verifiable: the held_item field says who it is.
    """
    from pokeagent.teaching import Teacher

    def _target(dd):
        return next((m for m in dd.state.party()
                     if not m.is_egg
                     and dd.names.species(m.species).upper() == species.upper()),
                    None)

    def _holder(dd):
        return next((m for m in dd.state.party()
                     if not m.is_egg and m.held_item == 182), None)

    take_share_from_anyone(d)
    want = _target(d)
    if want is None:
        log.info("  %s is not in the party", species)
        return None
    holder = _holder(d)
    if holder is not None and holder.nickname == want.nickname:
        return holder.nickname

    # AIM AT THE TARGET, then CHECK. The picker is unreliable on the party
    # screen's two-column grid, and giving the share to a fighter wastes the
    # whole run: NINJA L58 held it for a full gauntlet while MARILL -- the
    # mon that needed one level to become Azumarill -- earned nothing.
    # Each give SWAPS held items, so retrying is safe and self-correcting.
    t = Teacher(d)
    for attempt in range(3):
        t.give_to_mon("EXP SHARE", want.nickname)
        holder = _holder(d)
        if holder is not None and holder.nickname == want.nickname:
            log.info("  the share is held by %s L%s (attempt %d)",
                     holder.nickname, holder.level, attempt + 1)
            return holder.nickname
        if holder is not None:
            log.info("  the share landed on %s, not %s -- retrying",
                     holder.nickname, want.nickname)
    if holder is None:
        log.info("  the share landed on nobody (%s)",
                 getattr(t, "last_reason", "?"))
        return None
    log.info("  giving up: the share is on %s, not %s", holder.nickname,
             want.nickname)
    return None


#: The League is a straight chain of single warps, alternating (5,2) and
#: (6,2), read out of each map's own warp_events. `travel` cannot route it --
#: asked for the Champion from Corridor5 it planned BACKWARD through
#: SidneysRoom and gave up ("no approach to warp (6,2)"). Walking the chain
#: explicitly is both shorter and reliable.
LEAGUE_CHAIN = [
    ("EverGrandeCity_PokemonLeague", 9, 1),
    ("EverGrandeCity_Corridor5", 5, 2),
    ("EverGrandeCity_SidneysRoom", 6, 2),
    ("EverGrandeCity_Corridor1", 5, 2),
    ("EverGrandeCity_PhoebesRoom", 6, 2),
    ("EverGrandeCity_Corridor2", 5, 2),
    ("EverGrandeCity_GlaciasRoom", 6, 2),
    ("EverGrandeCity_Corridor3", 5, 2),
    ("EverGrandeCity_DrakesRoom", 6, 2),
    ("EverGrandeCity_Corridor4", 5, 2),
]
CHAMPION_ROOM = "EverGrandeCity_ChampionsRoom"
#: ChampionsRoom (6,12) goes back to Corridor4; (6,2) is the Hall of Fame,
#: which must NOT be entered -- it ends the run and resets the Elite Four.
CHAMPION_EXIT = (6, 12)


def walk_league_chain(d, upto=CHAMPION_ROOM, tries=3) -> bool:
    """Warp forward room by room until we stand in `upto`."""
    for _ in range(len(LEAGUE_CHAIN) + 4):
        here = d.map_name()
        if here == upto:
            return True
        step = next((c for c in LEAGUE_CHAIN if c[0] == here), None)
        if step is None:
            log.info("    not on the League chain (%s)", here)
            return False
        _m, x, y = step
        # SYNC THE LIVE GRID FIRST. A beaten room runs
        # `ResetAdvanceToNextRoom` on load, which OPENS (6,1)/(6,2) with
        # `setmetatile` at runtime (pret/data/scripts/elite_four.inc:31-40)
        # -- and the decomp's own comment says this path is "only necessary
        # when re-entering an Elite Four room after defeating the member,
        # which isnt normally possible", which is precisely what we are doing.
        # The STATIC .blk still reads those tiles as wall, so nav refuses the
        # door that is standing wide open. `sync_grid` pushes the drift in.
        try:
            drift = d.sync_grid()
            if drift:
                log.info("    synced %d changed cells at %s", drift, here)
        except Exception as exc:  # noqa: BLE001 - never fatal
            log.debug("    sync_grid: %s", str(exc)[:70])
        # `walk_door`, NOT `take_warp`. These doors sit against the top wall
        # and take_warp refused every one of them -- "no approach to warp
        # (6,2) on EverGrandeCity_SidneysRoom". elite_four already solved it:
        # walk to a walkable NEIGHBOUR of the door and step onto it, which is
        # also the only thing that fires a warp.
        from elite_four import walk_door

        # IF THE DOOR IS SHUT, THE TRAINER IS ALIVE -- FIGHT THEM.
        # The room seals on entry (`WalkInCloseDoor` sets VAR_ELITE_4_STATE=1
        # and makes the six entry tiles impassable) and the NORTH door is only
        # opened by the victory script. So an unreachable (6,2) is not a
        # routing failure, it is an unbeaten Elite Four member standing at
        # (6,5). Measured on the live state: all four
        # FLAG_DEFEATED_ELITE_4_* were FALSE with VAR_ELITE_4_STATE=1, while
        # `elite_four.py` was cheerfully logging "already beaten -- walking
        # out" and leaving the building. That mistake is what turned a 70,000
        # exp gauntlet into one level a pass.
        reach = set()
        try:
            reach = {(t[0], t[1]) for t in d.nav.reachable(here, d.pos())}
        except Exception as exc:  # noqa: BLE001
            log.debug("    reachable: %s", str(exc)[:70])
        if reach and (x, y) not in reach:
            log.info("    door (%d,%d) is shut -- engaging the trainer", x, y)
            try:
                d.talk_to(6, 5)
                d.advance_scene(40_000)
                if d.in_battle():
                    d.fight()
                    d.advance_scene(60_000)
                    log.info("    beat the room; party %s",
                             [(m.nickname, m.level) for m in d.state.party()
                              if not m.is_egg])
                # Step off the trainer's face or the next A re-opens dialogue.
                for mv in ("D", "L", "R"):
                    if d.step_dir(mv):
                        break
                d.advance_scene(40_000)
                d.sync_grid()
            except Exception as exc:  # noqa: BLE001
                log.info("    trainer: %s", str(exc)[:110])

        moved = False
        for attempt in range(max(tries, 5)):
            # RE-SYNC BETWEEN ATTEMPTS. The victory script opens the north
            # door with `setmetatile` DURING its own animation
            # (`SetAdvanceToNextRoomMetatiles`: Delay32, SE_DOOR, then the
            # tiles, then DrawWholeMapView -- elite_four.inc:1-17), so a grid
            # synced the instant the battle ends still shows a wall. Beating
            # Glacia and then reporting "stuck heading for (6,2)" was exactly
            # this: the door was opening while we measured it shut.
            if attempt:
                d.advance_scene(40_000)
                try:
                    d.sync_grid()
                except Exception as exc:  # noqa: BLE001
                    log.debug("    re-sync: %s", str(exc)[:70])
            if walk_door(d, (x, y), ("U", "L", "R", "D")):
                moved = True
                break
        if not moved:
            log.info("    stuck at %s heading for (%d,%d)", here, x, y)
            return False
        d.advance_scene(40_000)
        # A room whose trainer is unbeaten seals and starts a battle.
        if d.in_battle():
            d.fight()
            d.advance_scene(40_000)
    return d.map_name() == upto


def steven_fights(d, rounds: int = 6) -> int:
    """Fight the Champion over and over. This is the only XP up here.

    All four Elite Four flags are SET, so their rooms just open and nothing
    is fought -- `ResetEliteFour` only runs on entering the Hall of Fame
    (pret/data/event_scripts.s:776-782), which we deliberately do not do.
    The Champion, though, needs no reset: his ON_FRAME script is gated on
    VAR_TEMP_1, a TEMP var cleared on every map load, and the battle is
    `trainerbattle_no_intro TRAINER_STEVEN` with no defeated-flag guard
    (EverGrandeCity_ChampionsRoom/scripts.inc:19-43). Stepping out to
    Corridor4 and back in re-arms it, at 14,161 exp a run -- half of which
    the benched EXP. SHARE holder takes.
    """
    fought = 0
    for _ in range(rounds):
        if not walk_league_chain(d):
            break
        started = False
        for _ in range(16):
            d.advance_scene(40_000)
            if d.in_battle():
                started = True
                break
            d.emu.run_sequence("A:4 .:30")
        if not started:
            log.info("    no champion battle started at %s", d.map_name())
            break
        d.fight()
        d.advance_scene(60_000)
        fought += 1
        log.info("    champion fight %d done; party %s", fought,
                 [(m.nickname, m.level) for m in d.state.party()
                  if not m.is_egg])
        # OUT THE SOUTH DOOR, never (6,2): that is the Hall of Fame.
        from elite_four import walk_door

        if not walk_door(d, CHAMPION_EXIT, ("D", "L", "R", "U")):
            log.info("    could not leave the champion's room")
            break
        d.advance_scene(40_000)
    return fought


def run_gauntlet(state: str, minutes: float) -> int:
    here = Path(__file__).resolve().parent
    r = subprocess.run(
        # ITS OWN FEED NAME. `elite_four.py` defaults to `--feed default`,
        # which the wild sweeper usually owns, and the single-writer guard
        # then kills the gauntlet before a single punch is thrown:
        #   RuntimeError: live feed 'default' is already being written by ...
        #   gauntlet exited 1
        # Third time that guard has eaten a run from inside its own family.
        [sys.executable, str(here / "elite_four.py"),
         "--state", state, "--out", state,
         # NO --train. It sends the bench IN, and a L22 against Sidney's
         # L46-55 faints -- which earns NOTHING, because a fainted
         # participant is skipped outright
         # (pret/src/battle_script_commands.c:3436). Watched it happen:
         # "me 58->0", "me 62->0", ODDISH and NATU wiped in one pass.
         # The EXP. SHARE is the entire point: a BENCHED holder is paid
         # `calculatedExp / 2` and never risks a hit.
         "--minutes", str(minutes),
         "--feed", "evolve"],
        cwd=str(here.parent),
    )
    return r.returncode


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True)
    ap.add_argument("--minutes", type=float, default=180.0)
    ap.add_argument("--per-target", type=float, default=25.0)
    ap.add_argument("--only", default=None,
                    help="comma-separated species to attempt")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    targets = TARGETS
    if a.only:
        want = {s.strip().upper() for s in a.only.split(",")}
        targets = [t for t in TARGETS if t[0] in want]

    stop = time.time() + a.minutes * 60.0
    gained = []
    wanted = [t[0] for t in targets]

    # ONE PC TRIP FOR THE WHOLE BATCH. Doing it per species meant a flight
    # to a Centre and a re-climb of the plateau for every single target.
    d = Driver(a.state)
    d.advance_scene(40_000)
    dex = DexTarget(d.emu, d.names, d.consts, d.nav, spec=d.spec)
    log.info("start %s %s | %s", d.map_name(), d.pos(),
             dex.summary(d.state).split(";")[0])
    batch = swap_in_batch(d, dex, wanted, limit=5)
    log.info("batch in party: %s", batch)
    # BACK UP TO THE PLATEAU. The PC is in a town Centre and the gauntlet is
    # above a waterfall behind Victory Road, so leaving the state at Lilycove
    # made `elite_four.py` exit 1 on every target -- the share was correctly
    # held and nothing was ever fought.
    if batch:
        try:
            import league_run

            if not league_run.on_plateau(d):
                league_run.to_city(d)
                league_run.climb(d)
            log.info("climbed back to %s %s", d.map_name(), d.pos())
            # AND THROUGH THE DOOR. The climb ends OUTSIDE on the plateau, and
            # from there `elite_four.py` skipped every single room --
            # "SKIP EverGrandeCity_Corridor1 -- standing on EverGrandeCity" --
            # so the gauntlet exited 1 without a punch thrown. `into_hall`
            # is the helper that enters the building.
            import league_loop

            if not d.map_name().startswith("EverGrandeCity_"):
                league_loop.into_hall(d)
                log.info("entered %s %s", d.map_name(), d.pos())
        except Exception as exc:  # noqa: BLE001
            log.info("climb: %s", str(exc)[:110])
    d.save(a.state)
    del d

    if not batch:
        log.info("nothing could be withdrawn; stopping")
        return 1

    for species in batch:
        if time.time() >= stop:
            log.info("out of time before %s", species)
            break
        becomes = next((b for a_, b in targets if a_ == species), "?")
        log.info("=== %s -> %s ===", species, becomes)

        d = Driver(a.state)
        d.advance_scene(40_000)
        dex = DexTarget(d.emu, d.names, d.consts, d.nav, spec=d.spec)
        before_dex = dex_count(dex, d.state)
        mon = next((m for m in d.state.party()
                    if not m.is_egg
                    and d.names.species(m.species).upper() == species), None)
        if mon is None:
            log.info("  %s is not in the party any more -- skipping", species)
            del d
            continue
        nick = give_share_to_target(d, species)
        if nick is None:
            log.info("  nobody is holding the share -- skipping")
            del d
            continue
        d.save(a.state)
        del d

        left = max(1.0, (stop - time.time()) / 60.0)
        rc = run_gauntlet(a.state, min(a.per_target, left))
        log.info("  gauntlet exited %d", rc)

        # THE CHAMPION IS THE ACTUAL XP. The gauntlet above only walks the
        # room chain now that the four members are already beaten, so do the
        # repeatable Champion fight in-process -- no subprocess, so no feed
        # to fight over either.
        d2 = Driver(a.state)
        d2.advance_scene(40_000)
        if not d2.map_name().startswith("EverGrandeCity_"):
            try:
                import league_loop
                league_loop.into_hall(d2)
            except Exception as exc:  # noqa: BLE001
                log.info("  into_hall: %s", str(exc)[:90])
        n = steven_fights(d2, rounds=int(max(1, a.per_target // 6)))
        log.info("  champion fights: %d", n)
        d2.save(a.state)
        del d2

        d = Driver(a.state)
        d.advance_scene(40_000)
        dex = DexTarget(d.emu, d.names, d.consts, d.nav, spec=d.spec)
        now_dex = dex_count(dex, d.state)
        log.info("  party now: %s",
                 [(m.nickname, d.names.species(m.species), m.level)
                  for m in d.state.party() if not m.is_egg])
        log.info("  dex %d -> %d | %s", before_dex, now_dex,
                 dex.summary(d.state).split(";")[0])
        if now_dex > before_dex:
            gained.append((species, becomes, now_dex - before_dex))
        d.save(a.state)
        del d

    log.info("RESULT gained %s", gained)
    return 0

def take_share_from_anyone(d) -> bool:
    """Get the EXP. SHARE off whoever is holding it, back into the bag.

    Needed because the share persists across runs: the chain found it
    equipped on NINJA, so `give_share_to_target` could never find it in the
    ITEMS pocket ("teach: EXP SHARE is not in the ITEMS pocket") and every
    target was skipped with "nobody is holding the share".

    The party screen's per-mon menu (SUMMARY / ITEM / SWITCH / CANCEL, then
    GIVE / TAKE) is the only way to unequip. The picker geometry is still
    unsolved -- RSE draws slot 0 as a tall box with slots 1-5 in a grid, so
    asking for index N can land elsewhere. So do NOT trust the input: try
    each slot and judge by `held_item`, which is the same discipline that
    finally cracked the deposit.
    """
    SHARE = 182

    def holder(dd):
        return next((i for i, m in enumerate(dd.state.party())
                     if not m.is_egg and m.held_item == SHARE), None)

    start = holder(d)
    if start is None:
        return True                      # already in the bag (or gone)
    log.info("  the share is equipped on slot %d -- taking it back", start)

    for attempt in range(6):
        d.close_menus()
        d.settle(200)
        # START -> POKeMON
        d.emu.run_sequence("START:6 .:60")
        d.settle(400)
        if not d.menu.select_label("POK\xe9MON"):
            if not d.menu.select_label("POKEMON"):
                d.close_menus()
                continue
        d.settle(600)
        # walk the cursor down `attempt` times, then open the mon's menu
        for _ in range(attempt):
            d.emu.run_sequence("DOWN:6 .:24")
        d.settle(300)
        d.emu.run_sequence("A:6 .:90")
        d.settle(500)
        if d.menu.select_label("ITEM"):
            d.settle(500)
            if d.menu.select_label("TAKE"):
                d.settle(900)
                d.emu.run_sequence("A:6 .:120")   # clear "taken" dialogue
                d.settle(600)
        d.close_menus()
        d.settle(300)
        if holder(d) is None:
            log.info("  share is back in the bag (took it on attempt %d)", attempt + 1)
            return True
    log.info("  could not unequip the share (still on slot %s)", holder(d))
    return False


if __name__ == "__main__":
    raise SystemExit(main())
