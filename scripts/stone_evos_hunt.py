#!/usr/bin/env python
"""Rod hunt: STARYU (Lilycove City) and CORSOLA (Route 128 / Ever Grande).

A rod cast costs no STEP, only frames, so fishing is the cheapest encounter
source in the game -- and both of these species are 15% slot-2 SUPER ROD
entries on maps that are one Fly away:

    LilycoveCity super_rod: WAILMER, WAILMER, STARYU, WAILMER, WAILMER
    Route128/EverGrandeCity super_rod: LUVDISC, WAILMER, CORSOLA, WAILMER, WAILMER

Two things this has to get right, both learned the hard way by peers:

* **Resolve the battle at the TOP of every cast.** `Fishing.fish` opens with
  `if self.d.in_battle(): return self._fail("cast-failed", "already in a
  battle")` (pokeagent/fishing.py:866-867), so a single unanswered hook turns
  every later cast in the loop into the same log line forever. So the battle
  is drained before the cast, and again whenever `fish()` returns False while
  `in_battle()` is true -- which is the normal way a hook is reported when
  the reel outcome and `in_battle()` disagree by a few frames.
* **Turn, do not step.** `goto` puts us on the shore cell; aiming at the water
  with `step_dir` would WALK onto it. A four-frame tap turns in place, the
  same trick `collect.fish_map` uses (scripts/collect.py:403-413).

Everything else is delegated: `Driver.fish` plays the reel window,
`Catcher.plan/policy` decides the ball (a dex-new species bypasses the ball
reserve outright, catching.py:249-254), and anything already in the dex is
FLED from rather than fought -- 85% of the hooks on these two tables are
WAILMER, and fighting a level-45 one to death costs more than the cast did.
"""

import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pokeagent import dex as dexmod  # noqa: E402
from pokeagent import team as teammod  # noqa: E402
from pokeagent.catching import Catcher  # noqa: E402
from pokeagent.trek import Driver  # noqa: E402

log = logging.getLogger("rod_hunt")

#: ``(species id, name, [(map, staging Fly landing)])``.
#:
#: EVER GRANDE IS A TRAP for CORSOLA even though it carries the same
#: super-rod table as Route 128 and is a Fly destination: MEASURED, the Fly
#: landing is (18,6) up on the PLATEAU, `sync_grid` changes zero cells there
#: and `water_edges` finds NO reachable fishable shore -- the beach is below
#: the waterfall in a different walkable component. Worse, `travel` out of
#: that landing walks into the dungeon and then reports "no walkable route
#: from VictoryRoad_B1F to Route128: indoors and could not step outside".
#: So Route 128 is entered by surf from the NORTH, which is one map hop from
#: the Mossdeep landing.
LEGS = [
    (120, "STARYU", [("LilycoveCity", "LilycoveCity")]),
    (222, "CORSOLA", [("Route128", "MossdeepCity"),
                      ("Route128", "PacifidlogTown"),
                      ("Route128", "LilycoveCity")]),
]

ROD = "SUPER ROD"


class Hunt:
    def __init__(self, driver):
        self.d = driver
        self.target = dexmod.DexTarget(
            driver.emu, driver.names, driver.consts, driver.nav,
            spec=driver.spec,
        )
        self.team = teammod.Team(driver.names, driver.consts, driver.state)
        self.catcher = Catcher(driver, self.team)
        #: per-leg accounting, reported at the end
        self.casts = 0
        self.hooks = 0
        self.encounters = {}
        self.spot = None

    # ---- dex ----------------------------------------------------------

    def caught(self) -> set:
        c, _seen = self.target.dex_flags(self.d.state)
        return c

    def natdex(self, species_id) -> int | None:
        entry = self.target.by_species.get(species_id)
        return entry.natdex if entry else None

    def has(self, species_id) -> bool:
        nat = self.natdex(species_id)
        return bool(nat) and nat in self.caught()

    # ---- battles ------------------------------------------------------

    def enemy_species(self, frame=None):
        try:
            enemy = (frame or self.d.battle_frame() or {}).get("enemy") or {}
            return enemy.get("species") or enemy.get("name")
        except Exception:  # noqa: BLE001 - a diagnostic never ends a battle
            return None

    def table_species(self, map_name=None) -> set:
        """Every species name the current map's encounter tables can roll."""
        name = map_name or self.d.map_name()
        try:
            rows = self.target.wild.for_map(name)
        except Exception:  # noqa: BLE001 - not every map has a table
            return set()
        return {self.d.names.species(r.species) for r in rows}

    def settled_frame(self, frames=2400):
        """A battle frame whose ENEMY BLOCK is real.

        MEASURED, and the reason this is not `state.battle_ready()`:
        `frame()["enemy"]` is `gBattleMons[1]` (battle.py:461), which the
        intro has not written yet. Reading it as soon as `battle_ready()`
        went true reported the LAST battle's leftovers -- "ZIGZAGOON L3",
        against a hook whose own turn log then said
        "T1 flee RUN | me 296->296 | WAILMER 121->121". Deciding a catch off
        that would have FLED a STARYU, which is the whole errand.

        Two gates, both cheap: the action menu is up (so the intro has run
        and a policy is about to be asked anyway), and the species is one
        this map can actually roll.
        """
        d = self.d
        allowed = self.table_species()
        best = None
        spent = 0
        while spent < frames and d.in_battle():
            if d.battle.at_action_menu():
                frame = d.battle_frame()
                best = frame or best
                sp = self.enemy_species(frame)
                if sp and (not allowed or sp in allowed):
                    return frame
            d.emu.tick(20)
            spent += 20
        if not d.in_battle():
            return None
        return best or d.battle_frame()

    #: Ball preference for the fallback throw policy: best first, because a
    #: dex-new species is worth the good ball and the run holds 999,999.
    BALL_ORDER = ("ULTRA BALL", "NET BALL", "DIVE BALL", "TIMER BALL",
                  "GREAT BALL", "POKE BALL")

    def throw_policy(self):
        """Throw the best ball in the bag, every turn, no weakening.

        The fallback for a dex-new species the `Catcher` declined for any
        reason: a missed registration is not recoverable within this errand,
        and a thrown ball is.
        """
        def decide(_frame):
            balls = self.d.state.bag().get("poke_balls") or {}
            for name in self.BALL_ORDER:
                if balls.get(name):
                    return ("ball", name)
            return "flee"
        return decide

    def field_policy(self, frame):
        """The STANDING decision, installed on `Driver.battle_policy`.

        `battle_policy` is the only hook the package honours (trek.py:3162);
        `encounter_policy` is Crystal's API and has no consumer here. It
        matters because a `goto` that walks or surfs into a wild fights it
        with TACTICS by default -- which KOs a dex-new species before anyone
        can throw. This is asked ONCE PER TURN from the action menu, so the
        enemy block it reads is already populated and needs no gating.
        """
        enemy = frame.get("enemy") or {}
        species = enemy.get("species") or ""
        if not frame.get("wild", True):
            return None          # a trainer: hand it back to tactics
        try:
            new = species and not self.catcher.dex_caught(species)
        except Exception:  # noqa: BLE001
            new = False
        if new:
            balls = self.d.state.bag().get("poke_balls") or {}
            for name in self.BALL_ORDER:
                if balls.get(name):
                    log.info("[catch] %s is dex-new -- %s", species, name)
                    return ("ball", name)
        return "flee"

    def resolve_battle(self):
        """Play out whatever battle is live. Catch dex-new, flee the rest."""
        d = self.d
        if not d.in_battle():
            return None
        frame = self.settled_frame()
        if frame is None or not d.in_battle():
            return None
        name = self.enemy_species(frame)
        if name:
            self.encounters[name] = self.encounters.get(name, 0) + 1
        plan = None
        try:
            plan = self.catcher.plan(frame)
        except Exception as exc:  # noqa: BLE001
            log.info("[catch] plan raised: %s", str(exc)[:120])
        try:
            wanted = bool(name) and not self.catcher.dex_caught(name)
        except Exception:  # noqa: BLE001
            wanted = False
        if plan:
            log.info("[catch] %s -- %s", name, plan.reason)
            policy = self.catcher.policy(
                plan, inner=self.team.training_policy(tolerance=6,
                                                      safe_hp_frac=0.5))
        elif wanted:
            log.info("[catch] %s is dex-new and the catcher declined (%s) "
                     "-- throwing anyway", name,
                     getattr(plan, "reason", None) or "no plan")
            policy = self.throw_policy()
        else:
            why = getattr(plan, "reason", None) or "no battle frame"
            log.info("[hunt] fleeing %s (%s)", name, why)
            policy = lambda _frame: "flee"  # noqa: E731
        out = d.fight(policy=policy)
        # A fight can end on a level-up/evolution scene that still owns input;
        # the next cast would be refused from inside the bag if it did.
        for _ in range(4):
            if not d.scene_active():
                break
            d.advance_scene(40000)
        d.close_menus()
        return out

    # ---- the shore ----------------------------------------------------

    def water_edges(self, limit=60):
        """`[((x,y), face)]` -- cells we can stand on with fishable water in
        front of them, nearest first.

        `CanFish` (fishing.py:703-766) has TWO branches and so does this.
        On foot the standing cell must be land at elevation 3; **on a
        Pokemon's back the standing cell is water**, which matters because
        Route 128 is open ocean -- it has no land at all, so a land-only scan
        returns nothing there and the only place to fish Route 128's CORSOLA
        from is the saddle.
        """
        d = self.d
        here = d.map_name()
        surfing = d.is_surfing()
        try:
            reach = d.nav.reachable(here, d.pos(), d.elevation())
        except Exception as exc:  # noqa: BLE001
            log.info("[hunt] reachable(%s) failed: %s", here, str(exc)[:90])
            return []
        px, py = d.pos()
        rows = []
        for (x, y) in reach:
            stand = d.nav.cell(here, x, y)
            if stand is None:
                continue
            if d.nav._is_water(stand) != surfing:
                continue
            if not surfing and stand.elevation not in (3, 0, 15):
                continue
            for mv, (dx, dy) in (("U", (0, -1)), ("D", (0, 1)),
                                 ("L", (-1, 0)), ("R", (1, 0))):
                c = d.nav.cell(here, x + dx, y + dy)
                if c is None or not d.nav._is_water(c) or c.collision:
                    continue
                rows.append((abs(x - px) + abs(y - py), (x, y), mv))
                break
        rows.sort()
        return [(cell, mv) for _dist, cell, mv in rows[:limit]]

    def stand(self, spot) -> bool:
        """Get onto `spot`'s cell and FACE its water. Turn, never step."""
        d = self.d
        cell, face = spot
        if d.pos() != tuple(cell):
            if not d.goto(*cell, on_battle="fight"):
                return False
        for _ in range(3):
            if d.facing() == face:
                return True
            d.emu.run_sequence(f"{face}:4 .:12")
            if d.pos() != tuple(cell) and not d.goto(*cell, on_battle="fight"):
                return False
        return d.facing() == face and d.pos() == tuple(cell)

    def clear_field(self):
        """Nothing may own input when the bag is opened."""
        d = self.d
        for _ in range(3):
            if d.in_battle():
                self.resolve_battle()
                continue
            if not d.scene_active():
                break
            d.advance_scene(60000)
            d.close_menus()
        d.close_menus()

    # ---- the leg ------------------------------------------------------

    def travel_to(self, name, via=None, budget=420.0) -> bool:
        """Fly to `via`, then walk/surf to `name`.

        The staging landing is not an optimisation, it is the routing. Route
        128 has no Fly target of its own, and which landing you start from
        decides whether `travel` finds a sea road at all: from the Ever
        Grande plateau it walked into Victory Road and reported "no walkable
        route from VictoryRoad_B1F to Route128: indoors and could not step
        outside", while Mossdeep is one surf hop north of it.
        """
        d = self.d
        if d.map_name() == name:
            return True
        target = via or name
        if d.map_name() != target:
            try:
                if d.fly_to(target):
                    log.info("[hunt] flew to %s %s", d.map_name(), d.pos())
            except Exception as exc:  # noqa: BLE001
                log.info("[hunt] fly %s: %s", target, str(exc)[:120])
        if d.map_name() == name:
            return True
        deadline = time.time() + budget
        while time.time() < deadline and d.map_name() != name:
            was = (d.map_name(), d.pos())
            try:
                d.travel(name, on_battle="fight",
                         budget_s=min(120.0, deadline - time.time()))
            except Exception as exc:  # noqa: BLE001
                log.info("[hunt] travel %s: %s", name, str(exc)[:120])
            if (d.map_name(), d.pos()) == was:
                break
        return d.map_name() == name

    def hunt(self, species_id, species_name, maps, max_casts=200,
             budget_s=1800.0) -> bool:
        d = self.d
        if self.has(species_id):
            log.info("[hunt] %s is already CAUGHT -- skipping", species_name)
            return True
        deadline = time.time() + budget_s
        for map_name, via in maps:
            if not self.travel_to(map_name, via=via):
                log.info("[hunt] could not reach %s via %s (at %s %s)",
                         map_name, via, d.map_name(), d.pos())
                continue
            # A game-clear `setmaplayoutindex` rewrites collision on the
            # post-game maps and nav decodes the SHIPPED layout, so push the
            # live grid in before asking where the water is.
            try:
                changed = d.sync_grid()
                log.info("[hunt] sync_grid(%s): %d cells", map_name, changed)
            except Exception as exc:  # noqa: BLE001
                log.info("[hunt] sync_grid(%s): %s", map_name, str(exc)[:120])
            if self.fish_here(species_id, species_name, max_casts, deadline):
                return True
        return self.has(species_id)

    def fish_here(self, species_id, species_name, max_casts, deadline) -> bool:
        d = self.d
        candidates = self.water_edges()
        if not candidates:
            log.info("[hunt] no fishable shore reachable on %s from %s",
                     d.map_name(), d.pos())
            return False
        log.info("[hunt] %d shore candidates on %s, nearest %s",
                 len(candidates), d.map_name(), candidates[:3])
        pick = 0
        self.spot = None
        for cast in range(max_casts):
            if time.time() > deadline:
                log.info("[hunt] budget spent after %d casts", cast)
                return False
            # THE TRAP. `fish()` refuses outright while a battle is live, so
            # an unanswered hook would poison every remaining cast.
            if d.in_battle():
                self.resolve_battle()
                if self.has(species_id):
                    return True
            spot = self.spot or (candidates[pick] if pick < len(candidates)
                                 else None)
            if spot is None:
                log.info("[hunt] every shore candidate was refused")
                return False
            if not self.stand(spot):
                log.info("[hunt] could not stand at %s facing %s", *spot)
                self.spot = None
                pick += 1
                continue
            self.clear_field()
            ok, why = d.fishing.faces_fishable_water()
            if not ok:
                log.info("[hunt] %s: %s", spot, why)
                self.spot = None
                pick += 1
                continue
            self.casts += 1
            got = d.fish(ROD)
            if not got:
                reason = d.last_fish_reason
                if d.in_battle():
                    # A hook the reel loop and `in_battle()` disagreed about.
                    log.info("[hunt] cast %d reported %s but a battle is live",
                             self.casts, reason)
                    self.hooks += 1
                    self.resolve_battle()
                    if self.has(species_id):
                        log.info("[hunt] %s CAUGHT on cast %d",
                                 species_name, self.casts)
                        return True
                    continue
                if reason == "no-rod":
                    log.error("[hunt] no %s in the bag -- stopping", ROD)
                    return False
                if reason == "wrong-tile":
                    log.info("[hunt] tile refused: %s", d.last_fish_detail)
                    self.spot = None
                    pick += 1
                    continue
                if reason == "cast-failed":
                    log.info("[hunt] cast %d failed: %s",
                             self.casts, d.last_fish_detail)
                    self.clear_field()
                continue
            # A cast that hooked. This spot works; keep it.
            self.spot = spot
            self.hooks += 1
            self.resolve_battle()
            if self.has(species_id):
                log.info("[hunt] %s CAUGHT on cast %d (%d hooks)",
                         species_name, self.casts, self.hooks)
                return True
        return self.has(species_id)

    def save(self, path):
        for _ in range(8):
            if not self.d.scene_active():
                break
            self.d.emu.run_sequence("B:4 .:30")
        if self.d.scene_active():
            self.d.advance_scene(40000)
        if self.d.in_battle():
            log.error("[hunt] refusing to save inside a battle")
            return False
        if self.d.scene_active():
            log.error("[hunt] refusing to save: a script owns input")
            return False
        self.d.save(path)
        return True


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default="saves/rod.state")
    ap.add_argument("--out", default="saves/rod-out.state")
    ap.add_argument("--casts", type=int, default=200,
                    help="cast ceiling per species")
    ap.add_argument("--budget", type=float, default=2400.0,
                    help="seconds per species")
    ap.add_argument("--only", default="",
                    help="comma-separated species names to hunt")
    a = ap.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    if "line3" in a.state:
        raise SystemExit("refusing to drive the canonical line3.state")

    d = Driver(a.state)
    h = Hunt(d)
    before = len(h.caught())
    log.info("[hunt] booted at %s %s -- dex %d caught",
             d.map_name(), d.pos(), before)
    bag = d.state.bag().get("key_items") or {}
    if ROD not in bag:
        raise SystemExit(f"no {ROD} in the bag: {sorted(bag)}")

    only = {s.strip().upper() for s in a.only.split(",") if s.strip()}
    results = {}
    for species_id, name, maps in LEGS:
        if only and name not in only:
            continue
        self_casts, self_hooks = h.casts, h.hooks
        ok = h.hunt(species_id, name, maps, max_casts=a.casts,
                    budget_s=a.budget)
        results[name] = (ok, h.casts - self_casts, h.hooks - self_hooks)
        log.info("[hunt] %s: %s in %d casts (%d hooks) at %s %s",
                 name, "CAUGHT" if ok else "MISSED",
                 h.casts - self_casts, h.hooks - self_hooks,
                 d.map_name(), h.spot)
        h.save(a.out)

    h.save(a.out)
    after = len(h.caught())
    log.info("[hunt] done: dex %d -> %d; %s", before, after, results)
    log.info("[hunt] encounters: %s", h.encounters)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
