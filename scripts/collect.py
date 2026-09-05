#!/usr/bin/env python
"""Collect the Pokedex: go where the missing species are, and catch them.

The play loop cannot do this, and the reason is structural rather than a bug.
Its objective is the next badge, so `fish_for_dex` -- which only runs from
`grind_step` -- almost never runs, and hoisting it onto the hot path produced a
visible loop (see the revert note in `play.py:step`). Collection needs to own
its own travel and its own time budget, which is what this is.

The shape:

* ask the dex which achievable species are not CAUGHT yet (the flag, not the
  party -- 63 species were seen-but-never-caught when this started);
* group them by the map that yields them and by METHOD, because a rod species
  cannot be walked to;
* visit maps best-first by how many missing species they close, flying to the
  nearest landing and walking the rest;
* on each map, pace grass or cast at water until its species are closed or the
  budget runs out, then move on.

Every stage is bounded. A map that will not cooperate costs a known number of
seconds and then loses its turn -- the failure mode this replaces is a single
`goto` sitting in one place for 280 seconds while nothing else happens.
"""

import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from pokeagent import dex as dexmod
from pokeagent.watchdog import StallWatch  # noqa: E402
from pokeagent.live import LiveFeed  # noqa: E402
from pokeagent.mart import Mart  # noqa: E402
from pokeagent import team as teammod  # noqa: E402
from pokeagent.catching import Catcher  # noqa: E402
from pokeagent.trek import Driver, TravelInterrupted  # noqa: E402

log = logging.getLogger("collect")

#: Encounter kinds a rod is needed for.
ROD_KINDS = {"old_rod": "OLD ROD", "good_rod": "GOOD ROD",
             "super_rod": "SUPER ROD"}

#: Encounter kinds that happen on WATER rather than grass. The dex renames a
#: water table on an Underwater* map to "dive" (dex.py:603), so checking for
#: "water" alone missed every underwater map -- which is where CHINCHOU,
#: CLAMPERL and RELICANTH live, and the collector visited Underwater1/2 four
#: times reporting "no reachable grass" before this was noticed.
WATER_KINDS = {"water", "dive"}


class Collector:
    def __init__(self, driver, per_map=420.0, casts=16, paces=90,
                 feed_name="default"):
        self.d = driver
        # PUBLISH. The widget watches `live/default.png`, and swapping the play
        # loop for this driver froze it for 43 minutes -- reported from the
        # couch as "its been dead for a while?", which is exactly right: the
        # emulator was working and nothing could see it. Anything that drives
        # the game for a long time owns the feed while it does.
        # ONE PUBLISHER PER EMULATOR. A Driver opened on a path under `saves/`
        # already attached a feed publishing to the very name the widget
        # watches, and a second attach raises outright -- which killed this
        # collector on launch. play.py learned this already; the collector
        # never did.
        # AN EXPLICIT --feed WINS. A Driver opened on saves/outdoors.state
        # auto-attaches a feed named after the STATE FILE (trek.py:1716), so
        # reusing whatever it found meant this published to
        # `live/outdoors.*` while the widget watched `live/default.*` --
        # making the --feed flag a lie and leaving the panel on a stale feed
        # that some other process had last written. Re-point it instead.
        existing = getattr(driver, "feed", None)
        if existing is not None and feed_name and existing.name != feed_name:
            log.info("re-pointing the feed from %r to %r",
                     existing.name, feed_name)
            existing.detach()
            existing = None
        self.feed = existing or (
            LiveFeed(feed_name).attach(driver) if feed_name else None)
        # WATCH THE PICTURE, NOT THE EMULATOR. A run pinned on one tile keeps
        # ticking and keeps publishing, so the only outward symptom is that
        # the screen stops changing -- which is how it gets reported, twice
        # now, by a human watching the widget. The watchdog reads the same
        # published feed the human does (never the core, which is not
        # thread-safe) and lets `pace_map` abandon a map instead of pressing
        # a refused direction until the session ends.
        self.watch = StallWatch(feed_name=feed_name or "default",
                                log=log).start()
        self.per_map = per_map
        self.casts = casts
        self.paces = paces
        self.target = dexmod.DexTarget(
            driver.emu, driver.names, driver.consts, driver.nav,
            spec=driver.spec,
        )
        self.team = teammod.Team(driver.names, driver.consts, driver.state)
        self.catcher = Catcher(driver, self.team)
        self.mart = Mart(driver)
        self.caught_here = 0
        #: Maps this run has proved it cannot route to. Kept per RUN, not
        #: persisted: reachability depends on where we are standing and on
        #: which field moves are mounted, so a map that was unreachable from
        #: one shore may be fine later. Without it the plan -- ordered by
        #: species-per-hop -- re-picked the same richest-but-unreachable map
        #: on every pass and never got to the other fifty.
        self._unroutable: set[str] = set()

    # ---- what is missing -------------------------------------------------

    def rods(self) -> set:
        keys = {k.replace(" ", "_").upper()
                for k in (self.d.state.bag().get("key_items") or {})}
        return {kind for kind, item in ROD_KINDS.items()
                if item.replace(" ", "_") in keys}

    def missing(self) -> set:
        caught, _seen = self.target.dex_flags(self.d.state)
        out = set()
        for entry in self.target.achievable:
            nat = getattr(entry, "natdex", None)
            if nat and nat not in caught:
                out.add(entry.species)
        return out

    def plan(self) -> list:
        """`[(map_name, kinds, species)]`, most species first."""
        want = self.missing()
        rods = self.rods()
        by_map = {}
        # `for_map` is the only accessor, so walk the map index (394 maps).
        for name in self.d.nav.index:
            try:
                slots = self.target.wild.for_map(name)
            except Exception:  # noqa: BLE001 - not every map has a table
                continue
            for slot in slots:
                if slot.species not in want:
                    continue
                kind = getattr(slot, "kind", "")
                if kind in ROD_KINDS and kind not in rods:
                    continue          # no rod for it, so it is not available
                entry = by_map.setdefault(name, [set(), set()])
                entry[0].add(kind)
                entry[1].add(slot.species)
        rows = [(m, k, sp) for m, (k, sp) in by_map.items()]
        # Value per hop, not raw count: ten species six maps away is worth less
        # than seven species next door, and travel is where the time goes.
        here = self._graph_distances(self.d.map_name())
        rows.sort(key=lambda r: -len(r[2]) / (1.0 + here.get(r[0], 12)))
        return rows

    # ---- doing it --------------------------------------------------------

    def base_policy(self):
        """The training policy every battle here starts from."""
        return self.team.training_policy(tolerance=6, safe_hp_frac=0.5)

    def _graph_distances(self, target, max_hops=8) -> dict:
        """Map-to-map hop counts outward from `target`, over warps and seams.

        Static structure, cached per target. The only use is ranking fly
        landings, and getting it wrong is what made an earlier version of this
        pick a landing by NAME PREFIX -- which for "Route110" matched nothing
        and chose arbitrarily.
        """
        cache = getattr(self, "_graph_cache", None)
        if cache is None:
            cache = self._graph_cache = {}
        if target in cache:
            return cache[target]
        from collections import deque

        seen = {target: 0}
        queue = deque([(target, 0)])
        while queue:
            here, depth = queue.popleft()
            if depth >= max_hops:
                continue
            try:
                exits = self.d.nav.exits(here)
            except Exception:  # noqa: BLE001 - unreadable map ends the branch
                continue
            for e in exits:
                dest = e.get("dest")
                if not dest or dest in seen:
                    continue
                seen[dest] = depth + 1
                queue.append((dest, depth + 1))
        cache[target] = seen
        return seen

    def goto_map(self, name, budget=300.0) -> bool:
        # THE SAFARI ZONE IS NOT WALKABLE-TO. Its maps sit behind a gate that
        # wants 500 and a scripted entry, so plain routing reports
        # "could not reach SafariZone_Northwest" every time -- and Heracross,
        # Pinsir, Phanpy, Natu, Xatu, Pikachu, Wobbuffet, Doduo, Dodrio and
        # Rhyhorn all live back there. safari_probe already knows the door.
        if name.startswith("SafariZone") and not self.d.map_name().startswith(
                "SafariZone"):
            try:
                from safari_probe import reach_gate, enter

                if reach_gate(self.d, self) and enter(self.d):
                    log.info("entered the Safari Zone for %s (now %s)",
                             name, self.d.map_name())
            except Exception as exc:  # noqa: BLE001 - fall through to routing
                log.info("safari entry: %s", str(exc)[:80])
            # NO EARLY SUCCESS ON THE WRONG QUADRANT. This used to
            # `return map_name().startswith("SafariZone")`, so asking for
            # Northwest and landing in Southeast -- which is where the gate
            # puts you, always -- answered TRUE. The sweep then hunted
            # Southeast believing it was in Northwest, which is why NW's seven
            # species stayed missing even on the runs that "reached" it.
            # Getting through the door is progress, not arrival: fall through
            # and let the quadrant routing below finish the job.
        # AND ITS NORTH-WEST QUADRANT IS BEHIND A MUDDY SLOPE. On foot that
        # quadrant has ZERO aligned crossings with either neighbour: the only
        # corridor north out of Southwest (x=7..10) is severed at y=3,2 by two
        # MB_MUDDY_SLOPE tiles, and the x=19..23 pocket is entered from the
        # north. So DODUO, DODRIO, GOLDUCK, PINSIR, PSYDUCK, RHYHORN and
        # SEAKING were unreachable no matter how the sweep was routed, and no
        # run had ever stood there. safari_nw rides it.
        if name == "SafariZone_Northwest" \
                and self.d.map_name().startswith("SafariZone") \
                and self.d.map_name() != name:
            try:
                from safari_nw import climb_to_nw

                if climb_to_nw(self.d):
                    log.info("rode the slope into %s", self.d.map_name())
                    return True
                log.info("slope refused: %s", self.d.last_bike_reason)
            except Exception as exc:  # noqa: BLE001 - fall through to routing
                log.info("safari nw: %s", str(exc)[:80])
        # Inside the zone and after any climb: an ordinary quadrant hop.
        if name.startswith("SafariZone") \
                and self.d.map_name().startswith("SafariZone") \
                and self.d.map_name() != name:
            try:
                if self.d.travel(name, on_battle="fight", budget_s=budget):
                    return True
            except Exception as exc:  # noqa: BLE001
                log.info("safari hop to %s: %s", name, str(exc)[:80])
            return self.d.map_name() == name
        # AND IT IS NOT FLYABLE-OUT-OF. Fly is refused inside the Safari
        # Zone, so once the sweep finished in there it stayed: every later
        # map logged a fly attempt and then "no route to X from
        # SafariZone_Southeast", and the run blacklisted the entire rest of
        # the board without moving a step. Walk out through the entrance
        # first; Fly works again the moment we are on Route 121.
        if self.d.map_name().startswith("SafariZone") \
                and not name.startswith("SafariZone"):
            for door in ("Route121_SafariZoneEntrance", "Route121"):
                try:
                    self.d.travel(door, on_battle="fight", budget_s=120)
                except Exception as exc:  # noqa: BLE001
                    log.debug("safari exit via %s: %s", door, str(exc)[:70])
                if not self.d.map_name().startswith("SafariZone"):
                    log.info("walked out of the Safari Zone to %s",
                             self.d.map_name())
                    break
            else:
                log.info("still stuck in the Safari Zone at %s",
                         self.d.pos())
        # VICTORY ROAD IS ABOVE A WATERFALL. Ever Grande's plateau -- and the
        # dungeon door on it -- is only reachable by climbing at (18,68), which
        # plain routing has no concept of, so every attempt reported "could not
        # reach VictoryRoad_B1F" and the sweep quietly skipped the three maps
        # holding ARON, MAKUHITA, HARIYAMA, LAIRON, LOUDRED, WHISMUR, MEDITITE,
        # MEDICHAM, MAWILE and SABLEYE. league_run already does the climb.
        #
        # Worth being precise about why this is worth wiring: the dungeon gates
        # the League EXIT, not its wildlife. Encounter tables are per-map, so
        # the reachable parts -- 1F's southern region, B1F's entrance pocket,
        # and B2F through the proven (30,25) crossing -- give every one of those
        # species without solving the traversal at all.
        if name.startswith(("VictoryRoad", "EverGrandeCity")) \
                and not self.d.map_name().startswith(
                    ("VictoryRoad", "EverGrandeCity")):
            try:
                import league_run

                if league_run.to_city(self.d) and league_run.climb(self.d):
                    log.info("climbed to the plateau for %s", name)
                    if name == "EverGrandeCity":
                        return True
                    if self.d.take_warp(*league_run.VICTORY_ROAD_DOOR):
                        log.info("   entered %s", self.d.map_name())
            except Exception as exc:  # noqa: BLE001 - fall through to routing
                log.info("plateau entry: %s", str(exc)[:80])
        return self._goto_map(name, budget)

    def _goto_map(self, name, budget=300.0) -> bool:
        """Get to `name`, flying to the closest landing first.

        Bounded on purpose: a map that will not cooperate costs `budget`
        seconds and then loses its turn, instead of parking the whole session
        in one `goto` the way the reverted hot-path fishing did.
        """
        d = self.d
        if d.map_name() == name:
            return True
        deadline = time.time() + budget
        dist = self._graph_distances(name)
        here_cost = dist.get(d.map_name())
        try:
            landings = [t for t in d.fly_destinations()
                        if t.unlock_flag_name is None
                        or d.state.flag(t.unlock_flag_name)]
        except Exception:  # noqa: BLE001
            landings = []
        ranked = sorted(
            ((dist[t.map_name], t) for t in landings if t.map_name in dist),
            key=lambda r: r[0],
        )
        if ranked and (here_cost is None or ranked[0][0] < here_cost):
            cost, target = ranked[0]
            if target.map_name != d.map_name():
                log.info("   fly %s (%d hops out)", target.map_name, cost)
                d.fly_to(target.map_name)
        # NO ROUTE IS AN ANSWER, and it used to cost 300 seconds to not hear
        # it. This retried `travel` in a tight loop for the whole budget on a
        # map the planner had already proved unreachable -- up to 150s of each
        # attempt inside one pure-Python `route_legs`, with the emulator
        # advancing ZERO frames and the widget looking frozen. Profiled: 95%
        # of that is `reachable` fills (419k `step` calls for one 12-hop
        # query), and they cannot be memoised across cells.
        #
        # It bit hardest because the plan is ordered by species-per-hop, so
        # SafariZone_Northwest (7 species, the richest map on the board) was
        # picked FIRST every single pass -- and its quadrant is reachable only
        # through a corridor that is in a different walkable component from
        # where every crossing lands. Every pass wedged on the same map.
        probe = None
        try:
            probe = d.nav.route_legs(d.map_name(), d.pos(), name, max_hops=24)
        except Exception as exc:  # noqa: BLE001
            log.debug("route probe %s: %s", name, str(exc)[:70])
        if probe is None:
            self._unroutable.add(name)
            log.info("   no route to %s from %s (%s) -- skipping it this run",
                     name, d.map_name(), d.pos())
            return False
        idle = 0
        while time.time() < deadline:
            if d.map_name() == name:
                return True
            was = (d.map_name(), d.pos())
            left = deadline - time.time()
            try:
                d.travel(name, on_battle="fight", budget_s=min(60.0, left))
            except Exception as exc:  # noqa: BLE001 - a battle or a wall
                log.debug("travel %s: %s", name, str(exc)[:70])
            # A retry that moves nothing will not move anything next time
            # either: the planner is deterministic on a static grid.
            if (d.map_name(), d.pos()) == was:
                idle += 1
                if idle >= 3:
                    self._unroutable.add(name)
                    log.info("   %s: three travel attempts moved nothing "
                             "-- skipping it this run", name)
                    break
            else:
                idle = 0
        return d.map_name() == name

    def water_edge(self):
        d = self.d
        here = d.map_name()
        try:
            reach = d.nav.reachable(here, d.pos(), d.elevation())
        except Exception:  # noqa: BLE001
            return None
        best = None
        for (x, y) in sorted(reach):
            for mv, (dx, dy) in (("U", (0, -1)), ("D", (0, 1)),
                                 ("L", (-1, 0)), ("R", (1, 0))):
                c = d.nav.cell(here, x + dx, y + dy)
                if c is None or not d.nav._is_water(c) or c.collision:
                    continue
                dist = abs(x - d.pos()[0]) + abs(y - d.pos()[1])
                if best is None or dist < best[0]:
                    best = (dist, (x, y), mv)
        return (best[1], best[2]) if best else None

    def fish_map(self, deadline) -> int:
        d = self.d
        got = 0
        for _ in range(self.casts):
            if time.time() > deadline:
                break
            spot = self.water_edge()
            if spot is None:
                return got
            cell, face = spot
            if d.pos() != cell and not d.goto(*cell, on_battle="fight"):
                return got
            if d.facing() != face:
                # TURN, DO NOT STEP. `step_dir` holds the key and walks if the
                # tile is open, so aiming at the water walked ONTO the shore
                # cell instead -- after which the rod is pointed at floor and
                # every cast reports "wrong-tile: (17,45) facing U is floor".
                # A short tap turns in place.
                d.emu.run_sequence(f"{face}:4 .:12")
                if d.pos() != cell:
                    if not d.goto(*cell, on_battle="fight"):
                        return got
                    d.emu.run_sequence(f"{face}:4 .:12")
                if d.facing() != face:
                    continue
            # An unanswered box owns input and swallows START, which is what
            # "cast-failed: the bag would not USE SUPER ROD" actually was.
            # close_menus alone is not enough: a scene still RUNNING holds
            # sLockFieldControls and has to be walked to its end first.
            for _ in range(3):
                if not d.scene_active() and not d.in_battle():
                    break
                d.advance_scene(60000)
                if d.in_battle():
                    self.fight()
                d.close_menus()
            d.close_menus()
            if not d.fish():
                if d.last_fish_reason in ("no-rod",):
                    return got
                continue
            before = self._caught_count()
            self.fight()
            if self._caught_count() > before:
                got += 1
                self.save()
        return got

    def terrain_cells(self, terrain: str = "grass") -> list:
        """Reachable cells of this terrain on this map, nearest first.

        Wild land encounters only happen ON grass, and water encounters only
        on water. Pacing wherever the map was entered is why this caught
        nothing on Route 118 or 123 despite spending its whole budget there:
        the arrival cell was (10,13) and the grass runs from x=51 to x=55. A
        probe walked 219 laps on that spot without a single encounter.

        `terrain` exists because the grass-only version could not hunt a
        water species at all. Sent to Underwater2 for CHINCHOU, CLAMPERL and
        RELICANTH it reported "no reachable grass" and left -- and there is
        no grass anywhere underwater, so every dive and surf species in the
        game was unreachable by this collector.
        """
        d = self.d
        here = d.map_name()
        try:
            cells = set(d.nav.find_tiles(here, terrain))
            reach = set(d.nav.reachable(here, d.pos(), d.elevation()))
        except Exception:  # noqa: BLE001
            return []
        px, py = d.pos()
        return sorted(cells & reach,
                      key=lambda c: abs(c[0] - px) + abs(c[1] - py))

    def grass_cells(self) -> list:
        return self.terrain_cells("grass")

    def pace_map(self, deadline, terrain: str = "grass") -> int:
        """Walk this map's encounter terrain until its species close or time runs out.

        Walks with `goto` rather than hand-stepping. Hand-stepping spun 7.5
        MILLION times in 150 seconds without moving once: `step_dir` returns
        False instantly while a scene owns input
        ("scene-owns-input (gPlayerAvatar.preventStep)"), so the loop asked for
        a step, was refused for free, and asked again. `goto` crosses many
        grass cells per call, which is where encounters actually come from, and
        raises `TravelInterrupted` when one starts so the CATCH-aware battle
        code gets it instead of a bare `fight()`.
        """
        d = self.d
        got = 0
        # BOUND EVERY WALK BY THIS MAP'S BUDGET. goto only checks a deadline
        # if one is set, and pacing never set one, so a single call could
        # outlive the whole sweep -- which is exactly what happened on Victory
        # Road B1F: one goto spent hours re-clearing the same rock while the
        # loop below never got another turn to notice.
        # Per-WALK, not per-map: a single hop across a grass patch has no
        # business taking a quarter of an hour, and giving goto the whole map
        # budget let one call eat it entirely while the loop below waited for
        # its turn to notice the stall. Re-armed before each goto.
        walk_budget = 60.0
        # WATER IS ONLY TRAVERSABLE WITH `nav.surfing` SET. That flag is what
        # makes the walker treat a land->water step as a MOUNT (face the
        # water, A, answer YES -- trek.py:544-553) instead of a refused walk,
        # and it is also what lets BFS route over water at all. There is no
        # `surf()` to call: the mount is already automatic once nav will plan
        # through water.
        if terrain == "water":
            d.nav.surfing = True
        cells = self.terrain_cells(terrain)
        if not cells:
            log.info("   no reachable %s on %s", terrain, d.map_name())
            return 0
        log.info("   %d reachable %s cells, nearest %s",
                 len(cells), terrain, cells[0])
        stalled = 0
        i = 0
        while time.time() < deadline:
            if stalled >= 6:
                log.info("   pacing stalled on %s (%s)", d.map_name(),
                         d.last_goto_reason)
                break
            # The counter above only catches a loop that RETURNS. A walk that
            # never comes back -- goto retrying a step the engine refuses --
            # spends the whole budget without incrementing it, and the run
            # sits on one tile with the screen frozen. The watchdog sees that
            # from outside, off the published feed.
            if self.watch.stalled:
                log.info("   abandoning %s: %s", d.map_name(),
                         self.watch.detail)
                self.watch.clear()
                break
            if d.scene_active():
                d.advance_scene(40000)
                stalled += 1
                continue
            # Jump around the patch rather than to the nearest cell, so each
            # leg crosses grass instead of shuffling on one tile.
            i += 1
            target = cells[(i * 7) % len(cells)]
            if target == d.pos():
                continue
            try:
                d._journey_deadline = min(deadline, time.time() + walk_budget)
                if d.goto(*target, on_battle="raise"):
                    stalled = 0
                else:
                    stalled += 1
            except TravelInterrupted:
                before = self._caught_count()
                self.publish()
                self.fight()
                d.advance_scene(20000)
                stalled = 0
                if self._caught_count() > before:
                    got += 1
                    self.save()
            except Exception as exc:  # noqa: BLE001
                log.debug("pace: %s", str(exc)[:70])
                stalled += 1
        return got

    def fight(self):
        """Play the battle in front of us, as a CATCH chance first.

        The plan is computed ONCE from a settled frame, which is the whole
        difference between this working and not. Built per turn instead, every
        encounter of a 15-minute run reported "declined: trainer battle" with
        the enemy name reading `None` -- SHUPPET and CARVANHA among them, both
        wanted -- because `battle_frame()` has no `wild` flag until
        `state.battle_ready()` says the battle mon block is populated. That is
        the same "screens lie during transitions" rule the whole harness is
        built on, and it cost this run every catch it met for a quarter of an
        hour.
        """
        d = self.d
        for _ in range(80):
            if d.state.battle_ready():
                break
            d.emu.tick(20)
        if not d.in_battle():
            return None
        policy = self.base_policy()
        frame = None
        try:
            frame = d.battle_frame()
            plan = self.catcher.plan(frame) if frame else None
        except Exception as exc:  # noqa: BLE001 - never lose a battle here
            log.info("[catch] plan raised: %s", str(exc)[:90])
            plan = None
        if plan:
            log.info("[catch] going for it -- %s", plan.reason)
            policy = self.catcher.policy(plan, inner=policy)
        elif frame is None:
            # NOT a refusal -- the catcher was never asked. Distinguishing the
            # two matters: inside the Safari Zone this logged "declined None:"
            # with nothing after the colon, which reads like a broken reason
            # string when in fact `battle_frame()` returned None and `plan()`
            # never ran. The Safari sets BATTLE_TYPE_SAFARI rather than the
            # wild bit and `plan()` gates on `frame["wild"]`, so a Safari catch
            # decision has no path through the catcher at all today.
            log.info("[catch] no battle frame (battle_ready never came true) "
                     "-- the catcher was not consulted, in battle kinds %s",
                     getattr(d.state.battle(), "kinds", "unreadable"))
        else:
            # The REFUSAL CARRIES ITS OWN REASON. `plan()` returns a falsy
            # CatchPlan whose `.reason` is a full sentence, while `last_reason`
            # is only set on the early guards -- so reading `last_reason` here
            # printed "the catcher set no reason" against a decision that had
            # explained itself perfectly ("ODDISH adds nothing ... duplicates
            # GRASS the team already fields"). Ask the plan first.
            enemy = (frame.get("enemy") or {}).get("species") \
                or (frame.get("enemy") or {}).get("name")
            why = (getattr(plan, "reason", None)
                   or getattr(self.catcher, "last_reason", None)
                   or "no reason given")
            log.info("[catch] declined %s: %s", enemy, why)
        return d.fight(policy=policy)

    def _caught_count(self) -> int:
        try:
            caught, _ = self.target.dex_flags(self.d.state)
            return len(caught)
        except Exception:  # noqa: BLE001
            return -1

    def publish(self, msg=None):
        if self.feed is None:
            return
        try:
            if msg:
                self.feed.note(msg)
            self.feed.publish()
        except Exception as exc:  # noqa: BLE001 - a dead widget never stops a run
            log.debug("publish: %s", exc)

    def save(self):
        """Persist -- but never while a script owns input.

        A savestate taken mid-script is a landmine: it wedges every process
        that loads it afterwards, because `step_dir` is refused for free with
        "scene-owns-input (gPlayerAvatar.preventStep)" and nothing in the state
        says why. One such save cost two runs half an hour between them.
        """
        self.publish()
        for _ in range(8):
            if not self.d.scene_active():
                break
            self.d.emu.run_sequence("B:4 .:30")
        if self.d.scene_active():
            self.d.advance_scene(40000)
        if self.d.scene_active():
            log.info("   not saving: a script still owns input")
            return
        try:
            self.d.save(self.d.state_path)
        except Exception as exc:  # noqa: BLE001 - never lose the run to a save
            log.debug("save failed: %s", exc)

    #: Below this many balls, go shopping. The catcher keeps a reserve of 3
    #: and refuses at or under it, which is how a five-hour collection run
    #: declined SHUPPET, CARVANHA and every MAGIKARP it met with the stated
    #: reason "only 3 balls left (reserve 3)" -- the machinery was working
    #: perfectly and there was simply nothing to throw.
    BALL_FLOOR = 8
    #: Buy up to this many at a time; a Poke Ball is 200 and the run has 30k.
    #: Buy DEEP. A POKe BALL is 200, so a farmed 10,000 is fifty of them, and
    #: every farm/shop switch costs a fly plus a walk. Stocking 30 meant the
    #: run cycled back to farming after a handful of catches; the money was
    #: never the constraint once trainers were on the table.
    BALL_TARGET = 60

    def balls(self) -> int:
        return sum((self.d.state.bag().get("poke_balls") or {}).values())

    def nearest_mart(self):
        """The nearest shop, from the map index (same rule as the play loop)."""
        d = self.d
        here = d.map_name()
        best = None
        for name in d.nav.index:
            if not name.endswith("_Mart"):
                continue
            try:
                legs = d.nav.route_legs(here, d.pos(), name)
            except Exception:  # noqa: BLE001
                continue
            if legs is None:
                continue
            if best is None or len(legs) < best[0]:
                best = (len(legs), name)
        if best is not None:
            return best[1]
        # FLY TO THE TOWN INSTEAD. route_legs cannot plan INTO an indoor map,
        # so from Route115 it answered None for all eleven marts and the run
        # sat on "no Mart in reach; 0 balls" -- unable to catch anything, which
        # is the whole job. goto_map already knows how to fly to a landing and
        # walk in; pick a mart whose town is a known Fly destination.
        try:
            landings = {l.map_name for l in self.d.fly_destinations()}
        except Exception:  # noqa: BLE001
            return None
        for name in self.d.nav.index:
            if name.endswith("_Mart") and name.rsplit("_Mart", 1)[0] in landings:
                log.info("   no walking route to a Mart; flying to %s", name)
                return name
        return None

    def clerk_cell(self, mart_map):
        """The counter clerk, from the map's own object list."""
        try:
            info = self.d.nav.info(mart_map)
        except Exception:  # noqa: BLE001
            return None
        for obj in getattr(info, "objects", ()) or ():
            if "MART_EMPLOYEE" in str(obj.get("graphics_id", "")):
                return (int(obj["x"]), int(obj["y"]))
        return None

    #: Cheapest ball actually stocked in the late-game marts this run can
    #: reach. Mossdeep's shelf is ULTRA (1200) / NET (1000) / DIVE (1000) --
    #: there is no 200-money POKe BALL anywhere near here, so a lower bar just
    #: sends the collector shopping with money that buys nothing.
    CHEAPEST_BALL = 1000

    #: Above this per-ball price, go somewhere else first.
    CHEAP_BALL_CEILING = 400

    #: Marts that stock the 200-money POKe BALL. Badge-tier shelves in this
    #: game drop the basic ball entirely once a town is late enough.
    BASIC_MARTS = ("SlateportCity_Mart", "OldaleTown_Mart",
                   "PetalburgCity_Mart", "LavaridgeTown_Mart",
                   "VerdanturfTown_Mart", "MauvilleCity_Mart")

    def can_afford_a_ball(self) -> bool:
        return self.d.state.money() >= self.CHEAPEST_BALL

    #: Routes with trainer objects, richest first. Fighting one route funds
    #: dozens of balls; see scripts/trainer_farm.py for the measurement
    #: (130 -> 5,798 in fifteen minutes).
    FARM_ROUTES = ("Route119", "Route110", "Route109", "Route111", "Route117",
                   "Route118", "Route120", "Route121", "Route114", "Route113")

    def earn_money(self, budget_s=420.0) -> int:
        """Fight unbeaten trainers until we can afford a ball."""
        import trainer_farm

        # EARN A WHOLE RESTOCK, not one ball. Stopping at
        # `can_afford_a_ball()` meant the loop farmed a trickle, bought five
        # POKe BALLs, caught one species and went straight back to farming --
        # and each switch costs a fly plus a walk. A full bag is
        # BALL_TARGET * 200, so that is the target; routes running out of
        # unbeaten trainers ends it either way.
        want = self.BALL_TARGET * 200
        before = self.d.state.money()
        for route in self.FARM_ROUTES:
            if self.d.state.money() >= want:
                break
            try:
                if self.d.map_name() != route and not self.goto_map(route):
                    continue
                trainer_farm.farm_map(self.d, route, budget_s)
            except Exception as exc:  # noqa: BLE001
                log.info("   farming %s raised %s", route, type(exc).__name__)
                if self.d.in_battle():
                    self.d.fight(policy=Driver.damage_first)
        earned = self.d.state.money() - before
        log.info("   earned %d from trainers (money %d of %d wanted)", earned,
                 self.d.state.money(), want)
        self.save()
        return earned

    def restock_balls(self) -> bool:
        """Buy Poke Balls. Returns True only on a bag increase."""
        d = self.d
        have = self.balls()
        mart = self.nearest_mart()
        if mart is None:
            log.info("   no Mart in reach; %d balls", have)
            return False
        log.info("   %d balls left -- shopping at %s", have, mart)
        self.publish("shopping for balls at %s" % mart)
        if not self.goto_map(mart, budget=300.0):
            log.info("   could not reach %s (%s)", mart, d.last_goto_reason)
            return False
        cell = self.clerk_cell(mart)
        if cell is None:
            log.info("   no clerk on %s", mart)
            return False
        try:
            d.talk_to(*cell)
        except Exception as exc:  # noqa: BLE001
            log.info("   could not reach the clerk: %s", str(exc)[:60])
            return False
        d.settle(120)
        for _ in range(4):
            if self.mart.is_open():
                break
            d.emu.run_sequence("A:4 .:40")
        if not self.mart.is_open():
            log.info("   the clerk did not open a shop")
            d.emu.run_sequence("B:4 .:20 B:4 .:20")
            return False
        # Buy what this shop actually SELLS. Asking for "POKé BALL" failed with
        # "POKé BALL is not sold here": Fortree's mart is badge-6 tier and
        # stocks GREAT BALL (600) and ULTRA BALL (1200) with no basic ball on
        # the shelf at all. The run had 41,968 and came home with three balls.
        want = max(0, self.BALL_TARGET - have)
        ok = False
        if want:
            try:
                shelf = {r["name"].upper(): r["price"]
                         for r in self.mart.items()}
            except Exception:  # noqa: BLE001
                shelf = {}
            money = d.state.money()
            # Cheapest first: a catch is a catch, and thirty cheap balls beat
            # eight expensive ones.
            # READ THE SHELF, do not guess at names. Matching a hardcoded
            # list against the ROM's own strings fails on "POK\u00e9 BALL" --
            # the accented byte does not survive .upper() the same way -- and
            # the run ended up asking Oldale for an ULTRA BALL it does not
            # stock while a POK\u00e9 BALL sat on the shelf in front of it.
            # Anything the shop calls a BALL will do; cheapest first.
            balls_here = sorted((name for name in shelf if "BALL" in name),
                                key=lambda b: shelf[b])
            # DO NOT PAY LATE-GAME PRICES FOR A DEX BALL. Mossdeep's shelf is
            # ULTRA 1200 / NET 1000 / DIVE 1000 with no basic ball, and buying
            # the "cheapest" there spent 18,000 on EIGHTEEN balls -- the same
            # money is NINETY POKe BALLs at a basic-tier Mart. A dex sweep
            # wants quantity, not catch rate: most of what is left is ordinary
            # route fauna.
            if balls_here and shelf[balls_here[0]] > self.CHEAP_BALL_CEILING \
                    and mart not in self.BASIC_MARTS:
                log.info("   %s only sells %s at %d -- shopping basic instead",
                         mart, balls_here[0], shelf[balls_here[0]])
                try:
                    self.mart.leave()
                except Exception:  # noqa: BLE001
                    pass
                for basic in self.BASIC_MARTS:
                    if self.goto_map(basic, budget=240.0):
                        return self.restock_balls()
                log.info("   no basic Mart reachable; paying %s prices", mart)
            for ball in balls_here:
                price = shelf[ball]
                qty = min(want, money // price if price else 0)
                if qty <= 0:
                    continue
                log.info("   buying %dx %s at %d", qty, ball, price)
                ok = self.mart.buy(ball, qty)
                if ok:
                    break
                log.info("   %s: %s", ball, self.mart.last_reason)
        # Leave the shop VERIFIED, not after a fixed number of presses.
        # B-only, because blind A presses in a shop list BUY things (gotcha 13).
        # Four presses were not enough: the item DESCRIPTION box was still up
        # ("A good BALL with a higher catch rate than a POKé BALL."), the save
        # was taken with a script owning input, and every later run wedged on
        # it -- `step_dir` refused for free at FortreeCity_Mart (1,5) for
        # nineteen minutes, "0 battles, 0 steps", stall recovery firing.
        for _ in range(12):
            if not d.scene_active() and not self.mart.is_open():
                break
            d.emu.run_sequence("B:4 .:24")
        d.advance_scene(40000)
        now = self.balls()
        log.info("   balls %d -> %d (asked for %d, buy=%s)", have, now, want, ok)
        self.save()
        return now > have

    def pp_dry(self) -> bool:
        """Has the party run out of moves that can actually damage anything?

        HP is not the binding constraint on a collection run -- PP is. A
        restock attempt failed with "could not reach FortreeCity_Mart" after
        five minutes because every wild on the way became a war of attrition:
        the lead was down to SAND-ATTACK, the battle layer correctly reported
        "asked for SCARY FACE with no PP -- using SAND-ATTACK instead", and two
        moves got retired for changing nobody's HP. A party that cannot damage
        anything cannot travel, so it cannot shop, so it cannot catch.
        """
        d = self.d

        def can_hit(m) -> bool:
            for mid, pp in zip(m.moves or [], m.pp or []):
                try:
                    power = d.names.move_data(mid).power
                except Exception:  # noqa: BLE001
                    power = 0
                if power and pp:
                    return True
            return False

        party = [m for m in d.state.party()
                 if not getattr(m, "is_egg", False) and m.hp]
        if not party:
            return True
        # THE LEAD IS WHAT FIGHTS. "Somebody in the party can still hit
        # something" was the wrong test: the battle layer sends out slot 0, so
        # a dry LEAD makes tactics report "no usable move can touch ZIGZAGOON
        # (moveset: none)" and flee -- every encounter, including the dex-new
        # ones we came for. Found live with an L100 PELIPPER leading on empty
        # PP while five other party members were full, so the nurse was never
        # visited and the run fled its way across four routes catching nothing.
        if not can_hit(party[0]):
            return True
        return not any(can_hit(m) for m in party)

    def hurt(self) -> bool:
        """Is anything worth a trip to the nurse before the next map?

        The grass-pacing test ended with the lead at 0/104: a collection run
        that does not heal is a collection run that whites out, and a whiteout
        moves the player and costs money. The nurse restores PP as well, which
        is the other half of the reason to go.
        """
        if self.pp_dry():
            return True
        for m in self.d.state.party():
            if getattr(m, "is_egg", False):
                continue
            if m.max_hp and m.hp <= m.max_hp * 0.4:
                return True
        return False

    def heal(self, budget=240.0, tries=2) -> bool:
        """Nurse trip, bounded by ATTEMPTS as well as by the clock.

        The clock alone was not a bound: `heal_at_nearest_center` is one call
        that walks, fights and re-plans internally, so a `while time < deadline`
        loop around it can sit inside a single attempt indefinitely. It did --
        29 minutes with the log frozen on "healing first (LOTTAD 0/107,
        MIGHTYENA 0/121)" from inside a Mart. A budget that only applies
        between attempts is not a timeout.
        """
        deadline = time.time() + budget
        for _ in range(tries):
            if time.time() > deadline:
                break
            try:
                if self.d.heal_at_nearest_center(max_hops=6):
                    self.save()
                    return True
            except Exception as exc:  # noqa: BLE001 - a battle on the way
                log.debug("heal: %s", str(exc)[:70])
        log.info("   heal did not complete; carrying on hurt")
        return False

    def run(self, budget_s=3600.0, max_maps=12, only=None):
        """Collect until the budget runs out.

        `only` restricts the plan to a set of maps. Without it a Safari sweep
        walked OUT of the zone after its first area and spent the rest of its
        budget on Route 120, 111 and 118 -- the caller had filtered a plan for
        its own logging while `run` cheerfully rebuilt an unfiltered one.
        Inside the Safari that is worse than idle: every step outside the four
        areas still burns the zone's 500-step counter.
        """
        stop = time.time() + budget_s
        rows = self.plan()
        if only is not None:
            allowed = set(only)
            rows = [r for r in rows if r[0] in allowed]
        log.info("%d maps owe us species; %d species missing overall",
                 len(rows), len(self.missing()))
        visited = 0
        for name, kinds, species in rows:
            if name in self._unroutable:
                continue
            if time.time() > stop or visited >= max_maps:
                break
            if self.hurt():
                why = ("no damaging PP in the party" if self.pp_dry() else
                       ", ".join(f"{m.nickname} {m.hp}/{m.max_hp}"
                                 for m in self.d.state.party()
                                 if m.max_hp and m.hp <= m.max_hp * 0.4))
                log.info("   healing first (%s)", why)
                self.publish("healing: %s" % why)
                self.heal()
            # Shopping AFTER the nurse, on purpose: the first attempt at this
            # spent five minutes failing to reach a Mart with a party that had
            # nothing left to fight the road with.
            # DO NOT SHOP WHEN SHOPPING CANNOT WORK. With 5 balls, a 30-ball
            # target and 70 money the collector flew to the Mart, failed to
            # buy anything, flew back and did it again -- forever, never once
            # reaching the Safari Zone it had already picked. Shop only when
            # we are actually out, or when we can actually afford something.
            if self.balls() <= self.BALL_FLOOR:
                if not self.can_afford_a_ball():
                    # EARN FIRST. Wild battles pay NOTHING in Pokemon -- only
                    # trainers do -- so a collector that only paces grass can
                    # never refill its own balls, which is exactly how this run
                    # sat at 38/114 with 3 balls and 130 money for three
                    # sessions. 547 of 693 trainer flags were still unset.
                    self.earn_money()
                if self.balls() == 0 or self.can_afford_a_ball():
                    self.restock_balls()
                else:
                    log.info("   %d balls and %d money -- hunting anyway",
                             self.balls(), self.d.state.money())
            names = sorted(self.d.names.species(s) for s in species)
            headline = ("-> %s for %d: %s" % (
                name, len(species),
                ", ".join(names[:6]) + ("..." if len(names) > 6 else "")))
            log.info(headline)
            self.publish(headline)
            if not self.goto_map(name):
                log.info("   could not reach %s (%s)", name,
                         self.d.last_goto_reason)
                continue
            visited += 1
            deadline = min(stop, time.time() + self.per_map)
            before = self._caught_count()
            if kinds & set(ROD_KINDS):
                self.fish_map(deadline)
            # WATER AND LAND ARE DIFFERENT TERRAIN, and lumping them made
            # every surf and dive species uncatchable: the land pacer looked
            # for grass, found none underwater, and left. Split so each kind
            # is hunted where it actually lives.
            walk = kinds - set(ROD_KINDS)
            if walk & WATER_KINDS:
                self.pace_map(deadline, "water")
            if walk - WATER_KINDS:
                self.pace_map(deadline, "grass")
            log.info("   %s: caught %d (dex %d)", name,
                     self._caught_count() - before, self._caught_count())
            self.publish("%s: dex %d caught" % (name, self._caught_count()))
            self.save()
        log.info("done: dex %d caught", self._caught_count())


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True)
    ap.add_argument("--minutes", type=float, default=60.0)
    ap.add_argument("--per-map", type=float, default=420.0)
    ap.add_argument("--max-maps", type=int, default=12)
    ap.add_argument("--feed", default="default",
                    help="live feed name the widget watches ('' disables)")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    d = Driver(a.state)
    d.advance_scene(40000)
    Collector(d, per_map=a.per_map, feed_name=a.feed or None).run(
        budget_s=a.minutes * 60.0, max_maps=a.max_maps
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
