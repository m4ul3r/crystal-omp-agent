#!/usr/bin/env python
"""Attempt the Elite Four over and over until the Hall of Fame.

This loop exists because of one piece of geography: **a whiteout up here costs
nothing.** It returns the player to the last Center used, which on the upper
plateau is the League hall nurse -- so a lost gauntlet lands back beside the
nurse and the mart, fully healed, with no dungeon to re-cross. And the four
members we can already beat pay prize money: one attempt took the run from
\u00a5545 to \u00a510,672.

So the cycle funds itself:

    heal (free)  ->  spend everything on healing items  ->  fight
      ->  win: Hall of Fame
      ->  lose: whiteout puts us back at the nurse, richer than we started

Each pass also banks experience off L46-55 opponents, which is worth far more
than the wild grind on 1F -- that measured about one level per seven minutes.

The gauntlet itself is `elite_four.py`; this only handles the economy and the
repetition, so a failed attempt is a purchase rather than a setback.
"""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from pokeagent.trek import Driver, TravelInterrupted  # noqa: E402
from pokeagent.live import LiveFeed  # noqa: E402
from pokeagent.mart import Mart  # noqa: E402
from pokeagent.watchdog import StallWatch  # noqa: E402

log = logging.getLogger("loop")

HALL = "EverGrandeCity_HallOfFame"
LEAGUE = "EverGrandeCity_PokemonLeague"
PLATEAU = "EverGrandeCity"
NURSE = (3, 2)
CLERK = (16, 2)
LEAGUE_DOOR = (18, 5)

#: What to spend on, best HP-per-yen first. Measured against the actual
#: wallet this loop sees (~7,800 a pass):
#:
#:     HYPER POTION  200 HP / 1200  =  167 HP per 1000 yen  -> 6 for 7200
#:     MAX POTION    296 HP / 2500  =  118 HP per 1000 yen  -> 3 for 7500
#:
#: Six Hyper Potions is 1200 HP against three Max Potions' 888, so buying
#: "the better potion" was costing a third of the healing every attempt.
#: Only the lead's longevity matters here -- the other five are L48-54
#: against L46-55 and faint whatever we spend on them -- so raw HP per yen
#: is the right measure, not heal-to-full.
BASKET = (("HYPER POTION", 1200), ("MAX POTION", 2500))


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


VR_NORTH = "VictoryRoad_1F"
VR_EXIT = (39, 5)          # 1F -> EverGrandeCity(18,28), the UPPER plateau


def leave_a_room(d) -> bool:
    """Get out of an Elite Four room. There are exactly two ways, and
    walking back is not one of them.

    The rooms SEAL themselves. `EverGrandeCity_DrakesRoom_EventScript_
    WalkInCloseDoor` does `lockall` and calls
    `PokemonLeague_EliteFour_EventScript_WalkInCloseDoor`, so the door you
    came through is shut until its trainer is beaten. That is why an earlier
    "retreat to the hall" idea could never work: from Drake's (6,7) every
    approach to the (6,13) door stalled at (7,11) with the whole column
    reading collision 0, elevation 3, and no object in the way. Nothing was
    blocking it -- the map script was.

    So: engage the trainer. Win and the next door opens; lose and the whiteout
    returns us to the League nurse, healed, with the prize money kept. Either
    outcome leaves the loop somewhere it can work from, which is all this
    needs to do.
    """
    trainer = (6, 5)
    log.info("  sealed in %s -- engaging the trainer at %s", d.map_name(),
             trainer)
    before = d.map_name()
    guard(d, d.talk_to, *trainer)
    settle(d)
    if d.map_name() != before:
        log.info("  out the other side: %s %s", d.map_name(), d.pos())
        return True

    # STILL HERE: the trainer was ALREADY BEATEN, so talking to them is just
    # conversation -- no battle, no whiteout. The room's own script opens both
    # doors in that case (`call_if_set FLAG_DEFEATED_ELITE_4_*` ->
    # ResetAdvanceToNextRoom), so walk out. Up advances toward the Champion,
    # down retreats; either beats standing in a room we have already cleared.
    log.info("  %s is already beaten -- walking out", before)
    # Upper door first: forward is the only way out of this building.
    for cell in sorted(((w.x, w.y) for w in d.nav.info(before).warps),
                       key=lambda c: c[1]):
        for dx, dy, mv in ((0, 1, "U"), (0, -1, "D"), (-1, 0, "R"),
                           (1, 0, "L")):
            stand = (cell[0] + dx, cell[1] + dy)
            c = d.nav.cell(before, *stand)
            if c is None or c.collision:
                continue
            guard(d, d.goto, stand[0], stand[1], on_battle="fight")
            settle(d)
            if d.pos() != stand:
                continue
            guard(d, d.step_dir, mv)
            settle(d)
            if d.map_name() != before:
                log.info("  walked out to %s %s", d.map_name(), d.pos())
                return True
    log.info("  could not walk out of %s (at %s)", before, d.pos())
    return False


def into_hall(d) -> bool:
    if d.map_name() == LEAGUE:
        return True
    # Inside the League complex. Rooms are SEALED (win or whiteout); corridors
    # are ordinary maps and can be walked. Loop until we are out, because
    # leaving a room lands in a corridor and vice versa -- handling only one
    # of the two stranded the run in Corridor2 with 'could not reach the
    # League hall', which is the same gap that stranded fund_and_fight in
    # Corridor3 and made its farm earn nothing.
    for _ in range(12):
        name = d.map_name()
        if not name.startswith("EverGrandeCity_") or name == LEAGUE:
            break
        if name.endswith("Room"):
            if not leave_a_room(d):
                break
        elif "Corridor" in name:
            warps = [(w.x, w.y) for w in d.nav.info(name).warps]
            if not warps:
                break
            # FORWARD IS THE ONLY WAY OUT. The League complex is one-way:
            # the same setmetatile that seals a room's entrance also seals the
            # corridors behind you, so there is no walking back to the hall.
            # Proven the hard way -- from Corridor5(5,12) the run stepped onto
            # (4,12), which the map data lists as a warp to the hall, and the
            # map did not change. Three of its tiles claim to lead home and
            # none of them fire.
            #
            # So: advance. Beaten rooms are walk-through, the Champion ends it
            # one way or the other, and a whiteout lands on the plateau where
            # the nurse and the mart actually are. Trying to retreat instead
            # cost twenty-five "stuck" iterations per attempt.
            # TRY EVERY DOOR, LOWEST FIRST -- and never the one underfoot.
            # These corridors have THREE tiles leading home: Corridor5 warps
            # at (4,12), (5,12) and (6,12) all reach the hall. Arrival leaves
            # the player on the middle one, so a single step left or right
            # onto a sibling door fires it -- while picking that same tile and
            # calling take_warp on it did nothing, twenty-five times.
            here = d.pos()
            candidates = [c for c in sorted(warps, key=lambda c: c[1])
                          if c != here]
            for nb in ((-1, 0, "L"), (1, 0, "R"), (0, -1, "D"), (0, 1, "U")):
                dx, dy, mv = nb
                step_to = (here[0] + dx, here[1] + dy)
                if step_to in warps:
                    guard(d, d.step_dir, mv)
                    settle(d)
                    if d.map_name() != name:
                        log.info("  stepped through %s to %s %s", step_to,
                                 d.map_name(), d.pos())
                        break
            if d.map_name() != name:
                continue
            cell = candidates[0] if candidates else min(
                warps, key=lambda c: c[1])
            moved = False
            # ALREADY STANDING ON IT. Every arrival leaves the player on a
            # warp tile, and standing on one never fires it -- so the retreat
            # sat on Corridor5's (5,12) reporting "stuck" while the door home
            # was under its feet. take_warp is the one primitive that steps
            # off and re-enters.
            if d.pos() == cell:
                before2 = d.map_name()
                guard(d, d.take_warp, *cell)
                settle(d)
                if d.map_name() != before2:
                    log.info("  stepped back through %s to %s %s", cell,
                             d.map_name(), d.pos())
                    continue
            for dx, dy, mv in ((0, 1, "U"), (-1, 0, "R"), (1, 0, "L"),
                               (0, -1, "D")):
                stand = (cell[0] + dx, cell[1] + dy)
                c = d.nav.cell(name, *stand)
                if c is None or c.collision:
                    continue
                guard(d, d.goto, stand[0], stand[1], on_battle="fight")
                settle(d)
                if d.pos() != stand:
                    continue
                guard(d, d.step_dir, mv)
                settle(d)
                if d.map_name() != name:
                    moved = True
                    break
            if not moved:
                log.info("  stuck in %s at %s", name, d.pos())
                break
            log.info("  advanced to %s %s", d.map_name(), d.pos())
        else:
            break
    if d.map_name() == LEAGUE:
        return True
    # THE TRAINER LEAVES US INSIDE VICTORY ROAD. The plateau's own dungeon
    # door at (18,27) lands on 1F(39,5), so training rounds end on 1F -- and a
    # first version of this loop only knew the hall and the plateau, reported
    # "could not reach the League hall from VictoryRoad_1F", exited 1, and was
    # relaunched by restart=on-failure 33 times in nine seconds.
    if d.map_name() == VR_NORTH:
        # DO NOT pre-walk to "below the door": (39,6) is WALL. The warp at
        # (39,5) is entered from (38,5) or (40,5), and take_warp routes itself
        # to an adjacent cell anyway. Targeting the wall gave goto no path, and
        # it sat pinned at (22,31) for twenty-six minutes with the frame
        # counter climbing -- exactly the shape StallWatch exists to catch, in
        # a script that had not armed it.
        for _ in range(4):
            guard(d, d.take_warp, *VR_EXIT)
            settle(d)
            if d.map_name() == PLATEAU:
                break
    if d.map_name() == PLATEAU:
        guard(d, d.goto, 18, 6, on_battle="fight")
        for _ in range(4):
            guard(d, d.take_warp, *LEAGUE_DOOR)
            settle(d)
            if d.map_name() == LEAGUE:
                return True
    return d.map_name() == LEAGUE


def restock(d) -> dict:
    """Turn every spare yen into healing. Returns the bag afterwards."""
    m = Mart(d)
    guard(d, d.talk_to, *CLERK)
    for _ in range(4):
        d.advance_scene(20000)
        if m.is_open():
            break
    if m.is_open():
        for name, price in BASKET:
            n = d.state.money() // price
            if n <= 0:
                continue
            # Leave nothing behind: a failed run is only a purchase if the
            # money actually became items.
            # Buy as deep as the wallet goes. After a farming run the money
            # is the point: capping at 20 would leave it in the bank while
            # Drake wipes the party for want of healing.
            qty = min(n, 60)
            if m.buy(name, qty):
                log.info("  bought %d x %s", qty, name)
            else:
                log.info("  %s: %s", name, m.last_reason)
        m.leave()
    settle(d)
    return (d.state.bag() or {}).get("items") or {}


def heal(d) -> None:
    guard(d, d.talk_to, *NURSE)
    for _ in range(4):
        d.advance_scene(60000)
        d.close_menus()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True)
    ap.add_argument("--minutes", type=float, default=240.0)
    ap.add_argument("--feed", default="default")
    ap.add_argument("--train", action="store_true",
                    help="pass --train to each gauntlet attempt, so the bench "
                         "levels off the Elite Four while we try to win")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    stop = time.time() + a.minutes * 60.0
    here = Path(__file__).resolve().parent
    # Watch the published feed, not the emulator: a pinned run keeps ticking
    # and keeps publishing, so the only outward symptom is a frozen picture.
    watch = StallWatch(feed_name=a.feed or "default", log=log,
                       idle_s=240.0).start()

    attempt = 0
    while time.time() < stop:
        attempt += 1
        d = Driver(a.state)
        feed = None
        if a.feed:
            if getattr(d.emu, "observer", None) is not None:
                d.emu.observer = None
            feed = LiveFeed(a.feed)
            feed.attach(d)
        if d.map_name() == HALL:
            log.info("*** ALREADY IN THE HALL OF FAME ***")
            return 0
        log.info("=== attempt %d: %s %s money %d ===", attempt, d.map_name(),
                 d.pos(), d.state.money())
        # CLEAR WHATEVER OWNS THE INPUT FIRST. A nurse or clerk dialogue left
        # open from the previous pass eats every movement press, and `goto`
        # then reports no progress from a position it never left.
        settle(d)
        if not into_hall(d):
            log.info("could not reach the League hall from %s", d.map_name())
            return 1
        heal(d)
        items = restock(d)
        log.info("  items %s | money %d", items, d.state.money())
        log.info("  party %s", [(m.nickname, m.level, m.hp) for m in
                                d.state.party()])
        d.save(a.state)
        # RELEASE THE FEED, NOT JUST THE EMULATOR. `del d` drops the core but
        # leaves this process holding the feed's ownership sidecar, and this
        # process is still alive -- so the child's own `attach` was refused by
        # the single-writer guard and EVERY gauntlet attempt died instantly:
        #   RuntimeError: live feed 'xp' is already being written by pid ...
        #   gauntlet exited 1
        # Twelve attempts, zero battles. The guard is right (two writers
        # interleaving one file is the flicker it was added for); the parent
        # simply has to hand the feed over while the child owns the game.
        if feed is not None:
            try:
                feed.detach()
            except Exception as exc:  # noqa: BLE001 - never block the gauntlet
                log.debug("feed detach: %s", str(exc)[:70])
        del d                      # release the emulator before the child

        r = subprocess.run(
            [sys.executable, str(here / "elite_four.py"),
             "--state", a.state, "--out", a.state,
             "--minutes", "60", "--feed", a.feed]
            + (["--train"] if a.train else []),
            cwd=str(here.parent),
        )
        log.info("  gauntlet exited %d", r.returncode)
        if watch.stalled:
            log.info("  %s", watch.detail)
            watch.clear()

        d = Driver(a.state)
        where = d.map_name()
        log.info("  now %s %s money %d", where, d.pos(), d.state.money())
        won = where in (HALL, "EverGrandeCity_ChampionsRoom")
        del d
        if won:
            log.info("*** CHAMPION ***")
            return 0

    log.info("out of time after %d attempts", attempt)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
