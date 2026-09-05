#!/usr/bin/env python
"""Level the evolution targets on Champion Steven, from the bench.

This is the XP faucet the run has been missing. Every earlier grind fed
low-level mons on Route-102 wilds and they starved: a L15 kill split with a
L100 lead is worth almost nothing to a mon that needs L30, and a mon put in
FRONT to earn a full share gets one-shot.

The decompilation says there is a much better door, and it is repeatable:

* `EverGrandeCity_ChampionsRoom` starts the Steven battle from an ON_FRAME
  map script gated on `VAR_TEMP_1`, and VAR_TEMP_* is cleared on every map
  load -- and the battle is `trainerbattle_no_intro TRAINER_STEVEN` with NO
  defeated-flag guard (pret/data/maps/EverGrandeCity_ChampionsRoom/
  scripts.inc:19-43). So walking in starts it again, forever.
* His party totals 14,161 exp at the ×1.5 trainer rate, headed by a
  Metagross L58 / expYield 210 -- the single richest mon in the game
  (pret/src/data/trainer_parties.h:4480-4523).
* **EXP. SHARE PAYS A MON THAT NEVER ENTERS BATTLE.** Holders are counted at
  `battle_script_commands.c:3375`, paid `calculatedExp / 2 / viaExpShare` at
  `:3391`, and -- the part that matters -- the payout still sets
  `gLeveledUpInBattle` (`:3527`), so `TryEvolvePokemon`
  (`src/battle_main.c:5091-5113`) evolves it after the battle.

So: bench the target holding EXP. SHARE, let the L100 sweep, and it takes
7,080 exp per fight with zero faint risk and no switching logic. A L100
participant earns nothing but still inflates the divisor, which is why the
target is benched rather than dragged along.

Day-care levels are NOT a substitute: `ApplyDaycareExperience`
(pret/src/daycare.c:139-166) only calls `TryIncrementMonLevel` and never
checks evolution, so a day-care mon comes out over-levelled and unevolved.
"""
import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from pokeagent.trek import Driver, TravelError, TravelInterrupted  # noqa: E402

log = logging.getLogger("steven_xp")

ROOM = "EverGrandeCity_ChampionsRoom"
CORRIDOR = "EverGrandeCity_Corridor4"


def _party(d):
    return [m for m in d.state.party() if not m.is_egg]


def _summary(d):
    return ", ".join(f"{m.nickname}:{m.species}@L{m.level}" for m in _party(d))


def reach_room(d, budget=420.0) -> bool:
    """Get into the Champion's room. Arriving is what starts the fight.

    EVER GRANDE IS NOT WALKABLE-TO. The plateau sits above a waterfall at
    (18,68) and behind Victory Road, so plain routing answers "no walkable
    route from MauvilleCity to EverGrandeCity_ChampionsRoom" -- which is
    exactly what stopped the first run of this script. `league_run` already
    owns that climb; use it rather than re-deriving it here.
    """
    if d.map_name() == ROOM:
        return True
    if not d.map_name().startswith("EverGrande"):
        try:
            import league_run

            if not league_run.on_plateau(d):
                league_run.to_city(d)
                league_run.climb(d)
            log.info("league_run put us at %s %s", d.map_name(), d.pos())
        except Exception as exc:  # noqa: BLE001 - fall through to routing
            log.info("league_run: %s", str(exc)[:110])
    for _ in range(3):
        try:
            if d.travel(ROOM, on_battle="fight", budget_s=budget):
                return True
        except TravelInterrupted:
            d.fight()
            d.advance_scene(40_000)
        except TravelError as exc:
            log.info("travel %s: %s", ROOM, str(exc)[:110])
            break
        if d.map_name() == ROOM:
            return True
    return d.map_name() == ROOM


def one_fight(d) -> bool:
    """Walk in, let the ON_FRAME script fire, and play the battle out."""
    # Leave and re-enter so VAR_TEMP_1 is cleared and the script re-arms.
    if d.map_name() == ROOM:
        try:
            d.travel(CORRIDOR, on_battle="fight", budget_s=120)
        except Exception as exc:  # noqa: BLE001
            log.debug("step out: %s", str(exc)[:70])
    if not reach_room(d):
        return False
    # The script auto-walks the player and opens the battle; give it room.
    for _ in range(24):
        d.advance_scene(40_000)
        if d.in_battle():
            break
        d.emu.run_sequence("A:4 .:30")
    if not d.in_battle():
        log.info("no battle started in %s", d.map_name())
        return False
    d.fight()
    d.advance_scene(60_000)
    return True


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True)
    ap.add_argument("--minutes", type=float, default=60.0)
    ap.add_argument("--target", default=None,
                    help="party member to hand the EXP. SHARE to")
    ap.add_argument("--fights", type=int, default=0,
                    help="stop after N fights (0 = until the clock)")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    d = Driver(a.state)
    d.advance_scene(40_000)
    log.info("start %s %s", d.map_name(), d.pos())
    log.info("party: %s", _summary(d))

    # HAND THE EXP. SHARE TO THE TARGET FIRST. Without it a benched mon earns
    # nothing and the whole point is lost; the run then has to switch a L21
    # into a L58 Metagross to participate, which is how it dies.
    if a.target:
        from pokeagent.teaching import Teacher

        t = Teacher(d)
        if t.give_to_mon("EXP SHARE", a.target):
            log.info("exp share is held by %s", a.target)
        else:
            log.info("could not give the exp share to %s (%s) -- the grind "
                     "would earn it nothing, stopping", a.target,
                     getattr(t, "last_reason", "?"))
            return 1

    deadline = time.time() + a.minutes * 60.0
    fights = 0
    evolved = []
    before = {m.nickname: m.species for m in _party(d)}

    while time.time() < deadline and (not a.fights or fights < a.fights):
        if not one_fight(d):
            log.info("fight %d did not start; stopping", fights + 1)
            break
        fights += 1
        now = {m.nickname: m.species for m in _party(d)}
        for nick, sp in now.items():
            if before.get(nick) not in (None, sp):
                evolved.append((nick, before[nick], sp))
                log.info("EVOLVED %s: %s -> %s", nick, before[nick], sp)
        before = now
        log.info("fight %d done | %s", fights, _summary(d))
        # Heal between fights: the sweeper is the one that must survive, and
        # a wiped party turns the next walk-in into a whiteout.
        if any((m.hp or 0) <= 0 for m in _party(d)):
            log.info("party hurt; healing")
            d.heal_at_nearest_center()
        d.save(a.state)

    log.info("RESULT %d fights, %d evolutions: %s", fights, len(evolved),
             evolved)
    log.info("final party: %s", _summary(d))
    return 0 if fights else 1


if __name__ == "__main__":
    raise SystemExit(main())
