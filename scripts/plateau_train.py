#!/usr/bin/env python
"""Train the team on the League's doorstep, without re-crossing Victory Road.

Three Elite Four attempts died the same way: the level-100 lead carries every
battle until its PP runs out, and the other five (L48-54) are free KOs for a
L46-55 Elite Four -- each faint feeding the next. The fix is levels on the
other five, and the geography here makes that cheap.

Two facts that make this loop work:

* A whiteout returns the player to the last Center used, which up here is the
  **League hall nurse** -- so losing costs no progress and no re-crossing.
* Ever Grande's UPPER plateau has its own Victory Road door at (18,27), which
  lands on 1F(39,5) inside the 188-cell goal component. That component has
  grass with L40-45 wilds, and it is two warps from the nurse.

So: step into Victory Road, fight wilds with whoever is furthest behind in
front, walk back to the nurse when the party is hurt or dry, repeat.

The lead is switched to the laggard on turn one deliberately. Gen 3 splits
experience between participants, so fronting the mon that needs it is the
whole mechanism -- the same lever `scripts/balance.py` uses, aimed at a party
that has to survive five battles rather than at an even spread.
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
from pokeagent.live import LiveFeed  # noqa: E402

log = logging.getLogger("train")

PLATEAU = "EverGrandeCity"
VR_DOOR = (18, 27)          # upper-plateau door -> VictoryRoad_1F (39,5)
LEAGUE_DOOR = (18, 5)
NURSE = (3, 2)
GOAL_MAP = "VictoryRoad_1F"


def settle(d) -> None:
    for _ in range(8):
        if d.in_battle():
            d.fight()
            d.advance_scene(60000)
        elif d.scene_active():
            d.advance_scene(60000)
            d.close_menus()
        else:
            return


def guard(d, fn, *a, **k):
    for _ in range(4):
        try:
            return fn(*a, **k)
        except TravelInterrupted:
            settle(d)
    return None


def spread(d) -> list[tuple[str, int, int, int]]:
    return [(m.nickname, m.level, m.hp, m.max_hp) for m in d.state.party()]


def laggard(d, ceiling: int):
    """The party slot furthest behind, ignoring anyone already at the target."""
    best, idx = None, None
    for i, m in enumerate(d.state.party()):
        if m.hp <= 0 or m.level >= ceiling:
            continue
        if best is None or m.level < best.level:
            best, idx = m, i
    return idx, best


def hurt(d) -> bool:
    live = [m for m in d.state.party() if m.max_hp]
    if not live:
        return True
    if sum(1 for m in live if m.hp <= 0) >= 3:
        return True
    return any(m.hp <= 0.35 * m.max_hp for m in live if m.level >= 60)


def to_nurse(d) -> bool:
    """Back to the League hall and heal. Restores PP as well as HP."""
    if d.map_name() == GOAL_MAP:
        guard(d, d.goto, 39, 6, on_battle="fight")
        for _ in range(4):
            guard(d, d.take_warp, 39, 5)
            settle(d)
            if d.map_name() == PLATEAU:
                break
    if d.map_name() == PLATEAU:
        guard(d, d.goto, 18, 6, on_battle="fight")
        for _ in range(4):
            guard(d, d.take_warp, *LEAGUE_DOOR)
            settle(d)
            if "League" in d.map_name():
                break
    if "League" in d.map_name():
        guard(d, d.talk_to, *NURSE)
        for _ in range(4):
            d.advance_scene(60000)
            d.close_menus()
        return True
    return False


def into_victory_road(d) -> bool:
    if d.map_name() == GOAL_MAP:
        return True
    if "League" in d.map_name():
        guard(d, d.goto, 9, 10, on_battle="fight")
        for _ in range(4):
            guard(d, d.take_warp, 9, 11)
            settle(d)
            if d.map_name() == PLATEAU:
                break
    if d.map_name() == PLATEAU:
        guard(d, d.goto, VR_DOOR[0], VR_DOOR[1] + 1, on_battle="fight")
        for _ in range(4):
            guard(d, d.take_warp, *VR_DOOR)
            settle(d)
            if d.map_name() == GOAL_MAP:
                return True
    return d.map_name() == GOAL_MAP


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True)
    ap.add_argument("--out")
    ap.add_argument("--minutes", type=float, default=120.0)
    ap.add_argument("--ceiling", type=int, default=70)
    ap.add_argument("--feed", default="default")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    out = a.out or a.state
    stop = time.time() + a.minutes * 60.0

    d = Driver(a.state)
    if a.feed:
        if getattr(d.emu, "observer", None) is not None:
            d.emu.observer = None
        LiveFeed(a.feed).attach(d)
    log.info("START %s %s", d.map_name(), d.pos())
    log.info("spread %s", spread(d))

    # Front the laggard, and keep it in front: experience is split between
    # participants, so who starts the battle is who gains.
    # SWITCHING IS THE WHOLE MECHANISM, and the first version of this script
    # left it out: it identified the laggard, logged "training LOTTAD L48",
    # and then let the level-100 lead take every battle. LOTTAD gained nothing
    # across three rounds. Gen 3 splits experience between PARTICIPANTS, so
    # the mon that needs the levels has to be sent out.
    #
    # The "already switched" flag is per BATTLE, not per run -- holding it in
    # the outer closure switches once and never again (balance.py:56-58).
    def make_policy():
        done = {"switched": False, "battle": None}

        def policy(frame):
            me = (frame or {}).get("me") or {}
            turn = (frame or {}).get("turn")
            if turn is not None and turn <= 1:
                done["switched"] = False if done["battle"] != id(frame) else \
                    done["switched"]
            active = me.get("nickname") or me.get("name")
            pick = laggard(d, a.ceiling)
            if pick is not None:
                idx, mon = pick
                if active != mon.nickname and not done["switched"]:
                    done["switched"] = True
                    return ("switch", idx)
            try:
                analysis = d.outlook()
                if analysis is None:
                    return None
                action, _why = d.tactics.recommend(analysis, heal_at=0.4)
                return action
            except Exception:  # noqa: BLE001
                return None

        return policy

    d.battle_policy = make_policy()

    rounds = 0
    while time.time() < stop:
        rounds += 1
        if hurt(d):
            log.info("healing (%s)", spread(d))
            to_nurse(d)
            d.save(out)
        idx, who = laggard(d, a.ceiling)
        if who is None:
            log.info("every party member is at the ceiling %d", a.ceiling)
            break
        if not into_victory_road(d):
            log.info("could not get into Victory Road from %s", d.map_name())
            break
        log.info("round %d: training %s L%d", rounds, who.nickname, who.level)
        d.battle_policy = make_policy()   # fresh per round: see balance.py
        cells = d.nav.find_tiles(GOAL_MAP, "grass") \
            if hasattr(d.nav, "find_tiles") else []
        reach = {(x, y) for x, y, _ in
                 d.nav._reachable_triples(GOAL_MAP, d.pos(), d.elevation())}
        grass = [c for c in cells if c in reach]
        if not grass:
            log.info("no reachable grass from %s", d.pos())
            break
        for i in range(24):
            if time.time() > stop or hurt(d):
                break
            target = grass[(i * 7) % len(grass)]
            guard(d, d.goto, target[0], target[1], on_battle="fight")
            settle(d)
        d.save(out)
        log.info("  spread %s", spread(d))

    log.info("DONE %s %s", d.map_name(), d.pos())
    log.info("spread %s", spread(d))
    d.save(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
