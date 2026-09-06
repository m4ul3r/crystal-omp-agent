#!/usr/bin/env python
"""Three ordinary wild catches, as one idempotent chain leg.

    SPOINK   JaggedPass land  slots 4,6,9,11 -> 20%   (also unlocks GRUMPIG L32)
    ABSOL    Route120   land  slots 8,9      ->  8%
    CORSOLA  Route128   super_rod slot 7     -> 15% of hooks

Nothing here is clever; the value is that it can be run again. `--state PATH`
is MUTATED IN PLACE, every species is skipped when its CAUGHT flag is already
set, and the save is written the instant a flag flips -- so a leg that dies
half way still banked what it had, and chain.py can re-run it for free.

Three things this has to get right, all of them learned by peers on this line:

* **`d.battle_policy`, not `fight(policy=...)`.** `goto`/`travel` play their
  own interrupting encounters by calling `fight()` with no policy at all
  (trek.py:3184 reads `self.battle_policy`), and pacing grass means EVERY
  encounter is one of those. A policy passed only to my own `fight()` calls
  is absent exactly when it matters, and tactics KO a dex-new mon before
  anyone throws. `encounter_policy` is Crystal's name and has no consumer.
* **Never judge a catch off the first readable battle frame.**
  `frame["enemy"]` is `gBattleMons[1]`, which the intro has not written yet,
  so a fresh battle reads the PREVIOUS one's mon. Two cheap gates instead:
  the action menu is up, and the species read is one this map's encounter
  table can actually roll.
* **Drain the battle at the TOP of every cast.** `Fishing.fish` opens with
  `if self.d.in_battle(): return self._fail("cast-failed", "already in a
  battle")` (fishing.py:866), so one unanswered hook turns every later cast
  into that same log line forever.

Slot percentages are the engine's, not folklore: `ChooseWildMonIndex_Land`
gives 20/20/10/10/10/10/5/5/4/4/1/1 and `ChooseWildMonIndex_Fishing`'s
SUPER_ROD arm gives 40/40/15/4/1 over slots 5..9 (pret, src/wild_encounter.c).
The script prints the map's whole table with those weights before hunting it,
so the log proves what was being hunted.

No explicit LiveFeed. `Driver(state)` already auto-attaches one named after
the save's stem and `LiveFeed._claim` hard-errors on a second owner of a
name; a leg that publishes under its own name is one collision away from
dying in under a second. Autofeed is enough.
"""

import argparse
import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from pokeagent.dex import DexTarget  # noqa: E402
from pokeagent.trek import Driver, TravelInterrupted  # noqa: E402

log = logging.getLogger("wild_trio")

#: `ChooseWildMonIndex_*` weights, per slot index, by table kind.
SLOT_WEIGHTS = {
    "land": (20, 20, 10, 10, 10, 10, 5, 5, 4, 4, 1, 1),
    "water": (60, 30, 5, 4, 1),
    "rock_smash": (60, 30, 5, 4, 1),
    "old_rod": (70, 30),
    "good_rod": (60, 20, 20),
    "super_rod": (40, 40, 15, 4, 1),
}
#: Where each kind's slot numbering starts inside the shared fishing table.
SLOT_BASE = {"old_rod": 0, "good_rod": 2, "super_rod": 5}

#: Behaviors the ENGINE refuses on foot even though the metatile's collision
#: bits are clear, and which `nav.step` does not model at all.
#:
#: `CheckForPlayerAvatarCollision` (pret/src/field_player_avatar.c:606-611)
#: takes a collision of 0 and then calls `check_acro_bike_metatile`, which
#: overrides it to 9..13 for MB_BUMPY_SLOPE and the four rail behaviors
#: (:661-672, table at :172-180). Only the ACRO bike's wheelie states cross
#: those; walking never does.
#:
#: nav knows one slope, MUDDY_SLOPE = 0xD0 (nav.py:84), so a bumpy slope
#: reads as ordinary floor: `reachable` claimed all 34 of Jagged Pass's grass
#: cells from the Route 112 door and every `goto` through the x=9 column died
#: as "stalled 12x at (9, 33)". Blocked by hand per map, through
#: `nav.blocked`, which `step` does honour (nav.py:540).
FOOT_HAZARDS = frozenset((
    0xD1,                            # MB_BUMPY_SLOPE
    0xD3, 0xD4, 0xD5, 0xD6,          # ISOLATED_VERTICAL/HORIZONTAL, V/H RAIL
))

#: `(species name, map, table kind, fly staging landings, approach)`.
#:
#: Route 120 hangs off Fortree, and its 422 grass cells are all reachable
#: from either seam -- an ordinary outdoor map.
#:
#: JAGGED PASS IS ONE-WAY DOWNHILL. Its Route 112 door is at the BOTTOM
#: (14,40) and, with the bumpy slopes above honoured, exactly 56 cells and
#: ZERO grass are reachable from it -- measured, against 247 cells and all 34
#: grass cells from the Mt Chimney door at the top. So SPOINK is not hunted
#: from the Fly landing at all: the leg rides the CABLE CAR and walks down.
#:
#: Route 128 has no Fly target of its own and EVER GRANDE IS A TRAP for it
#: even though it shares the table: the landing is up on the plateau,
#: `sync_grid` changes zero cells there and no fishable shore is reachable
#: (measured by a peer). Mossdeep is one surf hop north of Route 128.
HUNTS = (
    ("SPOINK", "JaggedPass", "land", ("LavaridgeTown",), "jagged_top"),
    ("ABSOL", "Route120", "land", ("FortreeCity", "LilycoveCity"), None),
    ("CORSOLA", "Route128", "super_rod",
     ("MossdeepCity", "PacifidlogTown", "EverGrandeCity"), None),
)

ROD = "SUPER ROD"
BALL_ORDER = ("ULTRA BALL", "NET BALL", "DIVE BALL", "TIMER BALL",
              "GREAT BALL", "POKE BALL", "POKé BALL")


# ---- dex ------------------------------------------------------------------

class Trio:
    def __init__(self, driver, state_path):
        self.d = driver
        self.state_path = state_path
        self.target = DexTarget(driver.emu, driver.names, driver.consts,
                                driver.nav, spec=driver.spec)
        self.by_name = {}
        for entry in getattr(self.target, "entries", ()) or ():
            try:
                self.by_name[driver.names.species(entry.species)] = entry
            except Exception:  # noqa: BLE001 - a name gap is not fatal
                continue
        #: per-leg accounting: encounters (battles resolved on the hunt map)
        self.encounters = {}
        self.casts = 0
        self.hooks = 0
        self.saw = {}
        # INSTALL IT ONCE, on the driver.
        driver.battle_policy = self.field_policy

    def caught(self) -> set:
        c, _seen = self.target.dex_flags(self.d.state)
        return c

    def natdex(self, name):
        entry = self.by_name.get(name)
        return getattr(entry, "natdex", None) if entry else None

    def has(self, name) -> bool:
        nat = self.natdex(name)
        return bool(nat) and nat in self.caught()

    def dex_new(self, species_name) -> bool:
        """True when this species' CAUGHT flag is clear. Name -> natdex ->
        flag; species IDS and DEX NUMBERS are different namespaces and mixing
        them is the documented way to get a silently-always-False test."""
        nat = self.natdex(species_name)
        if not nat:
            return False
        return nat not in self.caught()

    # ---- tables -----------------------------------------------------------

    def table(self, map_name, kind=None):
        """`[(slot, kind, species name, weight, levels)]` for a map."""
        try:
            rows = self.target.wild.for_map(map_name)
        except Exception as exc:  # noqa: BLE001 - not every map has a table
            log.info("   no encounter table for %s: %s", map_name,
                     str(exc)[:90])
            return []
        out = []
        for r in rows:
            rk = getattr(r, "kind", "") or ""
            if kind and rk != kind:
                continue
            weights = SLOT_WEIGHTS.get(rk, ())
            idx = int(getattr(r, "slot", -1)) - SLOT_BASE.get(rk, 0)
            pct = weights[idx] if 0 <= idx < len(weights) else None
            try:
                name = self.d.names.species(r.species)
            except Exception:  # noqa: BLE001
                name = str(r.species)
            out.append((getattr(r, "slot", -1), rk, name, pct,
                        (getattr(r, "min_level", None),
                         getattr(r, "max_level", None))))
        return out

    def show_table(self, map_name, kind, want) -> float:
        """Log the table and return `want`'s total encounter share."""
        rows = self.table(map_name, kind)
        share = {}
        for slot, rk, name, pct, lvl in rows:
            share[name] = share.get(name, 0) + (pct or 0)
            log.info("   %-12s %-9s slot %2s  %3s%%  L%s-%s", map_name, rk,
                     slot, pct if pct is not None else "??", lvl[0], lvl[1])
        for name, pct in sorted(share.items(), key=lambda r: -r[1]):
            log.info("   = %-10s %3d%% of %s %s", name, pct, map_name, kind)
        got = share.get(want, 0)
        log.info("   HUNTING %s: %d%% of %s %s encounters", want, got,
                 map_name, kind)
        return got

    def map_species(self, map_name=None) -> set:
        return {r[2] for r in self.table(map_name or self.d.map_name())}

    # ---- battles ----------------------------------------------------------

    def balls(self) -> dict:
        try:
            return self.d.state.bag().get("poke_balls") or {}
        except Exception:  # noqa: BLE001
            return {}

    def pick_ball(self):
        balls = self.balls()
        for name in BALL_ORDER:
            if balls.get(name):
                return name
        for name, count in balls.items():
            if count:
                return name
        return None

    def field_policy(self, frame):
        """The STANDING decision: ball anything dex-new, flee everything else.

        Asked once per turn from the action menu, so the enemy block it reads
        is populated and needs no gating -- and re-deciding every turn is the
        cheap answer to a first turn judged on a stale mon: it corrects
        itself on turn two, while the target is still above zero because we
        never attack.
        """
        try:
            if not frame.get("wild", True):
                return None          # a trainer: hand it back to tactics
            enemy = frame.get("enemy") or {}
            species = enemy.get("species") or enemy.get("name") or ""
            if species and self.dex_new(species):
                ball = self.pick_ball()
                if ball:
                    return ("ball", ball)
                log.error("[catch] %s is dex-new and the bag has NO BALLS",
                          species)
        except Exception as exc:  # noqa: BLE001 - a policy must never raise
            log.debug("policy: %s", str(exc)[:80])
        return "flee"

    def settled_frame(self, frames=2400):
        """A battle frame whose ENEMY BLOCK is real."""
        d = self.d
        allowed = self.map_species()
        best = None
        spent = 0
        while spent < frames and d.in_battle():
            try:
                at_menu = d.battle.at_action_menu()
            except Exception:  # noqa: BLE001
                at_menu = False
            if at_menu:
                try:
                    frame = d.battle_frame()
                except Exception:  # noqa: BLE001
                    frame = None
                best = frame or best
                enemy = ((frame or {}).get("enemy") or {})
                sp = enemy.get("species") or enemy.get("name")
                if sp and (not allowed or sp in allowed):
                    return frame
            d.emu.tick(20)
            spent += 20
        if not d.in_battle():
            return None
        try:
            return best or d.battle_frame()
        except Exception:  # noqa: BLE001
            return best

    def resolve_battle(self, tag="") -> str | None:
        """Play out whatever battle is live; count it. Returns the species."""
        d = self.d
        if not d.in_battle():
            return None
        frame = self.settled_frame()
        name = None
        if frame:
            enemy = frame.get("enemy") or {}
            name = enemy.get("species") or enemy.get("name")
        if name:
            self.encounters[name] = self.encounters.get(name, 0) + 1
            self.saw[name] = self.saw.get(name, 0) + 1
        if d.in_battle():
            log.info("[battle%s] %s (%s)", tag, name or "?",
                     "DEX-NEW -- throwing" if name and self.dex_new(name)
                     else "known -- fleeing")
            try:
                d.fight(policy=self.field_policy)
            except Exception as exc:  # noqa: BLE001 - a battle never ends a leg
                log.info("[battle%s] fight raised: %s", tag, str(exc)[:120])
        self.clear_field()
        return name

    def clear_field(self):
        """Nothing may own input when we walk or open the bag."""
        d = self.d
        for _ in range(4):
            if d.in_battle():
                try:
                    d.fight(policy=self.field_policy)
                except Exception:  # noqa: BLE001
                    break
                continue
            if not d.scene_active():
                break
            d.advance_scene(60000)
            d.close_menus()
        try:
            d.close_menus()
        except Exception:  # noqa: BLE001
            pass

    # ---- saving -----------------------------------------------------------

    def bank(self, why="") -> bool:
        """Write the state file IN PLACE. Never from inside a battle."""
        d = self.d
        for _ in range(8):
            if not d.scene_active() and not d.in_battle():
                break
            if d.in_battle():
                self.resolve_battle(" pre-save")
            else:
                d.advance_scene(40000)
                d.close_menus()
        if d.in_battle() or d.scene_active():
            log.error("   refusing to save (%s): input is owned", why or "-")
            return False
        d.save(self.state_path)
        log.info("   banked %s (%s) at %s %s", self.state_path, why or "-",
                 d.map_name(), d.pos())
        return True

    # ---- getting there ----------------------------------------------------

    def block_foot_hazards(self, map_name) -> int:
        """Teach nav the cells the engine refuses on foot. See FOOT_HAZARDS."""
        d = self.d
        try:
            info = d.nav.info(map_name)
        except Exception as exc:  # noqa: BLE001
            log.info("   no map info for %s: %s", map_name, str(exc)[:90])
            return 0
        bad = set()
        for y in range(info.height):
            for x in range(info.width):
                cell = d.nav.cell(map_name, x, y)
                if cell is not None and cell.behavior in FOOT_HAZARDS:
                    bad.add((x, y))
        if not bad:
            return 0
        here = d.nav.blocked.setdefault(map_name, set())
        fresh = bad - here
        if fresh:
            here |= fresh
            d.nav._reach_cache.clear()
        log.info("   %s: %d bumpy-slope/rail cells refused on foot %s",
                 map_name, len(bad), sorted(bad)[:8])
        return len(fresh)

    def ride_cable_car(self) -> bool:
        """Route 112's station -> Mt Chimney. An NPC conversation, not a warp,
        so `route_legs` cannot plan through it (play.py:1216-1259)."""
        d = self.d
        if d.map_name() == "MtChimney_CableCarStation":
            return True
        for landing in ("MauvilleCity", "FallarborTown", "LavaridgeTown"):
            if d.map_name() == "Route112_CableCarStation":
                break
            if d.flight.flyable_here() and d.map_name() != landing:
                try:
                    d.fly_to(landing)
                except Exception as exc:  # noqa: BLE001
                    log.info("   fly %s: %s", landing, str(exc)[:100])
                    continue
            for _ in range(3):
                try:
                    d.travel("Route112_CableCarStation", on_battle="fight",
                             budget_s=150.0)
                except TravelInterrupted:
                    self.resolve_battle(" en route")
                except Exception as exc:  # noqa: BLE001
                    log.info("   travel station: %s", str(exc)[:110])
                    if d.in_battle():
                        self.resolve_battle(" en route")
                if d.map_name() == "Route112_CableCarStation":
                    break
        if d.map_name() != "Route112_CableCarStation":
            log.info("   could not reach the cable car station (on %s %s)",
                     d.map_name(), d.pos())
            return False
        log.info("   at the cable car station %s", d.pos())
        for _ in range(3):
            try:
                d.talk_to(6, 6)
            except Exception as exc:  # noqa: BLE001
                log.info("   talk_to(6,6): %s", str(exc)[:100])
            if d.choice_open():
                d.resolve_choice("YES")
            d.advance_scene(120000)
            d.settle(600)
            if d.map_name() == "MtChimney_CableCarStation":
                log.info("   rode the cable car up Mt Chimney")
                return True
            d.close_menus()
        return d.map_name() == "MtChimney_CableCarStation"

    def jagged_top(self) -> bool:
        """Stand on Jagged Pass at its Mt Chimney door, the only end of the
        map its grass is reachable from on foot."""
        d = self.d
        if d.map_name() == "JaggedPass" and self.grass_cells("JaggedPass"):
            return True
        if not self.ride_cable_car():
            return False
        if d.map_name() == "MtChimney_CableCarStation":
            try:
                d.take_warp(6, 11)
            except Exception as exc:  # noqa: BLE001
                log.info("   station door: %s", str(exc)[:100])
        if d.map_name() != "MtChimney":
            try:
                d.travel("MtChimney", on_battle="fight", budget_s=150.0)
            except Exception as exc:  # noqa: BLE001
                log.info("   travel MtChimney: %s", str(exc)[:110])
        if d.map_name() != "MtChimney":
            log.info("   not on MtChimney (on %s %s)", d.map_name(), d.pos())
            return False
        for door in ((20, 41), (21, 41)):
            try:
                d.take_warp(*door)
            except Exception as exc:  # noqa: BLE001
                log.info("   MtChimney door %s: %s", door, str(exc)[:100])
            if d.map_name() == "JaggedPass":
                log.info("   entered JaggedPass from the top at %s", d.pos())
                return True
        return d.map_name() == "JaggedPass"

    def surface(self) -> bool:
        """Come up from an Underwater map. A leg inherits the LAST leg's
        position and the underwater leg leaves us on Underwater1, where Fly
        is refused and nothing routes."""
        d = self.d
        for _ in range(4):
            if not d.map_name().startswith("Underwater") \
                    and not d.underwater():
                return True
            if not d.dive():
                log.info("   surface refused: %s", d.last_field_reason)
                # Not on a surfacable ceiling: shuffle to one that is.
                if not self.step_to_surfacable():
                    return False
        return not d.underwater()

    def step_to_surfacable(self) -> bool:
        """Walk to a ceiling the engine will let us surface through.

        `dive_gates` returns cells in RAW SCAN ORDER (nav.py:843), so taking
        its head walks to the map's top-left corner rather than to the nearest
        usable tile. Reachable-and-nearest first instead.
        """
        d = self.d
        here = d.map_name()
        d.nav.surfing = True
        try:
            gates = set(d.nav.dive_gates(here, "emerge"))
            reach = set(d.nav.reachable(here, d.pos(), d.elevation()))
        except Exception as exc:  # noqa: BLE001
            log.info("   dive_gates(%s): %s", here, str(exc)[:90])
            return False
        px, py = d.pos()
        cells = sorted(gates & reach,
                       key=lambda c: abs(c[0] - px) + abs(c[1] - py))
        log.info("   %d reachable emerge cells on %s, nearest %s", len(cells),
                 here, cells[:3])
        for xy in cells[:12]:
            if d.goto(*xy, on_battle="fight") and d.dive():
                return True
        return False

    def reach(self, map_name, landings, budget=540.0) -> bool:
        """Stand on `map_name`, from wherever this leg started."""
        d = self.d
        if d.map_name() == map_name:
            return True
        self.clear_field()
        self.surface()
        # Fly is refused indoors (Overworld_MapTypeAllowsTeleportAndFly), and
        # a chain leg can inherit a Pokemon Centre lobby.
        for _ in range(6):
            if d.flight.flyable_here():
                break
            try:
                if not d.flight.step_outside():
                    break
            except Exception:  # noqa: BLE001
                break
        deadline = time.time() + budget
        for landing in landings:
            if d.map_name() == map_name:
                return True
            if time.time() > deadline:
                break
            if d.map_name() != landing and d.flight.flyable_here():
                try:
                    d.fly_to(landing)
                    log.info("   flew to %s %s", d.map_name(), d.pos())
                except Exception as exc:  # noqa: BLE001
                    log.info("   fly %s refused: %s", landing, str(exc)[:110])
                    continue
            if d.map_name() == map_name:
                return True
            idle = 0
            while time.time() < deadline and d.map_name() != map_name:
                was = (d.map_name(), d.pos())
                try:
                    d._journey_deadline = None
                    d.travel(map_name, on_battle="fight",
                             budget_s=min(150.0, deadline - time.time()))
                except TravelInterrupted:
                    self.resolve_battle(" en route")
                    continue
                except Exception as exc:  # noqa: BLE001
                    log.info("   travel %s: %s", map_name, str(exc)[:130])
                    if d.in_battle():
                        self.resolve_battle(" en route")
                        continue
                if (d.map_name(), d.pos()) == was:
                    idle += 1
                    if idle == 1:
                        # Disbelieve nav's collision once: a game-clear
                        # `setmaplayoutindex` rewrites the live grid and nav
                        # decodes the SHIPPED layout.
                        try:
                            log.info("   sync_grid(%s): %d cells",
                                     d.map_name(), d.sync_grid())
                        except Exception as exc:  # noqa: BLE001
                            log.info("   sync_grid: %s", str(exc)[:90])
                        continue
                    if idle >= 3:
                        break
                else:
                    idle = 0
            if d.map_name() == map_name:
                return True
            log.info("   %s not reached from %s (sitting on %s %s)",
                     map_name, landing, d.map_name(), d.pos())
        # Last resort: walk to a neighbour and step onto the door by hand
        # (standing on a warp does not fire it -- trek.py:1972).
        try:
            neighbours = [e.get("dest") for e in d.nav.exits(map_name)
                          if e.get("dest")]
        except Exception:  # noqa: BLE001
            neighbours = []
        for src in dict.fromkeys(neighbours):
            if time.time() > deadline + 240:
                break
            if d.map_name() != src:
                try:
                    d.travel(src, on_battle="fight", budget_s=120.0)
                except Exception:  # noqa: BLE001
                    pass
                if d.map_name() != src:
                    continue
            try:
                d.sync_grid()
            except Exception:  # noqa: BLE001
                pass
            doors = [(int(e["x"]), int(e["y"])) for e in d.nav.exits(src)
                     if e.get("dest") == map_name]
            log.info("   on %s; %d door(s) into %s: %s", src, len(doors),
                     map_name, doors)
            for x, y in doors:
                try:
                    d.take_warp(x, y)
                except Exception as exc:  # noqa: BLE001
                    log.info("   warp (%d,%d): %s", x, y, str(exc)[:90])
                if d.map_name() == map_name:
                    return True
        return d.map_name() == map_name

    def maybe_heal(self):
        """Heal only when the party cannot fight. Fleeing is free, but a
        whiteout MOVES THE PLAYER, which loses the map."""
        try:
            party = self.d.state.party() or []
            alive = sum(1 for m in party if (getattr(m, "hp", 0) or 0) > 0)
        except Exception:  # noqa: BLE001
            return
        if alive > 2:
            return
        log.info("   %d mon(s) standing -- healing", alive)
        try:
            self.d.heal_at_nearest_center()
        except Exception as exc:  # noqa: BLE001
            log.info("   heal: %s", str(exc)[:110])

    # ---- land -------------------------------------------------------------

    def pace(self, want, map_name, deadline, approach=None) -> bool:
        """Walk the map's grass until `want` is CAUGHT or time is up."""
        d = self.d
        # LAND MEANS LAND. Leaving `nav.surfing` on from the trip out here
        # lets the planner route a "grass" hop across water, which paces the
        # wrong encounter table.
        d.nav.surfing = False
        d.nav._reach_cache.clear()
        stalled = 0
        i = 0
        cells = []
        since_scan = 0
        while time.time() < deadline:
            if self.has(want):
                return True
            if d.map_name() != map_name:
                log.info("   drifted to %s -- re-entering %s", d.map_name(),
                         map_name)
                if not self.reach(map_name, (map_name,), budget=240.0):
                    return self.has(want)
                d.nav.surfing = False
                cells = []
            if d.in_battle() or d.scene_active():
                self.clear_field()
                continue
            # RESCAN, don't trust one snapshot. Jagged Pass is one-way
            # downhill: every ledge we jump and every slope we slide puts
            # grass behind us permanently, so a list taken at the top is
            # mostly unreachable by the bottom.
            if not cells or since_scan >= 12:
                cells = self.grass_cells(map_name)
                since_scan = 0
                if not cells:
                    # Walked off the bottom of a one-way map: go round again.
                    if approach and time.time() < deadline:
                        log.info("   no grass left from %s -- re-approaching",
                                 d.pos())
                        if approach():
                            cells = self.grass_cells(map_name)
                    if not cells:
                        log.info("   no reachable grass on %s from %s",
                                 map_name, d.pos())
                        return self.has(want)
                log.info("   %d reachable grass cells on %s, nearest %s",
                         len(cells), map_name, cells[0])
            if stalled >= 4:
                log.info("   pacing stalled on %s (%s) -- rescanning",
                         map_name, d.last_goto_reason)
                cells = []
                stalled = 0
                self.clear_field()
                continue
            i += 1
            since_scan += 1
            # Jump ACROSS the patch, not to the nearest cell: encounters come
            # from crossing grass, and hopping to the neighbour shuffles.
            # Bounded to the nearest 80 so one leg of a 422-cell route does
            # not spend the whole budget walking to the far end of the map.
            pool = cells[:80]
            spot = pool[(i * 7) % len(pool)]
            if spot == d.pos():
                continue
            try:
                d._journey_deadline = min(deadline, time.time() + 45.0)
                if d.goto(*spot, on_battle="raise"):
                    stalled = 0
                else:
                    stalled += 1
            except TravelInterrupted:
                stalled = 0
                before = self.caught()
                self.resolve_battle()
                new = self.caught() - before
                if new:
                    log.info("   NEW DEX FLAGS: %s", sorted(new))
                    self.bank("caught %s" % sorted(new))
                    cells = []
                self.maybe_heal()
            except Exception as exc:  # noqa: BLE001
                log.debug("pace: %s", str(exc)[:90])
                stalled += 1
        return self.has(want)

    def grass_cells(self, map_name) -> list:
        d = self.d
        try:
            cells = set(d.nav.find_tiles(map_name, "grass"))
            reach = set(d.nav.reachable(map_name, d.pos(), d.elevation()))
        except Exception as exc:  # noqa: BLE001
            log.info("   grass scan on %s: %s", map_name, str(exc)[:90])
            return []
        px, py = d.pos()
        return sorted(cells & reach,
                      key=lambda c: abs(c[0] - px) + abs(c[1] - py))

    # ---- water ------------------------------------------------------------

    def water_edges(self, limit=40) -> list:
        """`[((x,y), face)]` -- cells we can stand on with fishable water in
        front, nearest first.

        `CanFish` has TWO branches and so does this: on foot the standing
        cell is LAND at elevation 3, on a Pokemon's back the standing cell is
        WATER -- and Route 128 is open ocean with no land at all, so a
        land-only scan finds nothing there.
        """
        d = self.d
        here = d.map_name()
        surfing = d.is_surfing()
        try:
            reach = d.nav.reachable(here, d.pos(), d.elevation())
        except Exception as exc:  # noqa: BLE001
            log.info("   reachable(%s): %s", here, str(exc)[:90])
            return []
        px, py = d.pos()
        rows = []
        for (x, y) in reach:
            stand = d.nav.cell(here, x, y)
            if stand is None:
                continue
            if d.nav._is_water(stand) != surfing:
                continue
            if not surfing and stand.elevation not in (0, 3, 15):
                continue
            for mv, (dx, dy) in (("U", (0, -1)), ("D", (0, 1)),
                                 ("L", (-1, 0)), ("R", (1, 0))):
                c = d.nav.cell(here, x + dx, y + dy)
                if c is None or not d.nav._is_water(c) or c.collision:
                    continue
                rows.append((abs(x - px) + abs(y - py), (x, y), mv))
                break
        rows.sort()
        return [(xy, mv) for _dist, xy, mv in rows[:limit]]

    def stand(self, spot) -> bool:
        """Get onto the cell and FACE its water. Turn, never step: aiming
        with a walk would step ONTO the water."""
        d = self.d
        xy, want = spot
        if d.pos() != tuple(xy):
            if not d.goto(*xy, on_battle="fight"):
                return False
        for _ in range(3):
            if d.facing() == want:
                return True
            d.emu.run_sequence(f"{want}:4 .:12")
            if d.pos() != tuple(xy) and not d.goto(*xy, on_battle="fight"):
                return False
        return d.facing() == want and d.pos() == tuple(xy)

    def fish_for(self, want, map_name, deadline, max_casts=400) -> bool:
        d = self.d
        d.nav.surfing = True
        candidates = self.water_edges()
        if not candidates:
            log.info("   no fishable water reachable on %s from %s (surfing=%s)",
                     map_name, d.pos(), d.is_surfing())
            return False
        log.info("   %d fishable spots on %s, nearest %s", len(candidates),
                 map_name, candidates[:3])
        pick = 0
        spot = None
        while self.casts < max_casts and time.time() < deadline:
            if self.has(want):
                return True
            # THE TRAP: an unanswered hook poisons every later cast.
            if d.in_battle():
                self.hooks += 1
                before = self.caught()
                self.resolve_battle(" hook")
                if self.caught() - before:
                    self.bank("caught %s" % sorted(self.caught() - before))
                if self.has(want):
                    return True
                continue
            if d.map_name() != map_name:
                log.info("   drifted to %s -- re-entering %s", d.map_name(),
                         map_name)
                if not self.reach(map_name, (map_name,), budget=240.0):
                    return self.has(want)
                d.nav.surfing = True
                candidates = self.water_edges()
                pick, spot = 0, None
                continue
            here = spot or (candidates[pick] if pick < len(candidates)
                            else None)
            if here is None:
                log.info("   every fishable spot was refused")
                return self.has(want)
            if not self.stand(here):
                log.info("   could not stand at %s facing %s", *here)
                spot = None
                pick += 1
                continue
            self.clear_field()
            ok, why = d.fishing.faces_fishable_water()
            if not ok:
                log.info("   %s not fishable: %s", here, why)
                spot = None
                pick += 1
                continue
            self.casts += 1
            hooked = d.fish(ROD)
            if not hooked:
                reason = d.last_fish_reason
                if d.in_battle():
                    # The reel outcome and `in_battle()` disagreed by frames.
                    continue
                if reason == "no-rod":
                    log.error("   no %s in the bag -- stopping", ROD)
                    return False
                if reason == "wrong-tile":
                    log.info("   tile refused: %s", d.last_fish_detail)
                    spot = None
                    pick += 1
                    continue
                if reason == "cast-failed":
                    log.info("   cast %d failed: %s", self.casts,
                             d.last_fish_detail)
                    self.clear_field()
                continue
            spot = here            # this spot works; keep it
            self.hooks += 1
            before = self.caught()
            self.resolve_battle(" cast %d" % self.casts)
            new = self.caught() - before
            if new:
                log.info("   NEW DEX FLAGS: %s", sorted(new))
                self.bank("caught %s" % sorted(new))
            if self.has(want):
                return True
        return self.has(want)

    # ---- one species ------------------------------------------------------

    def hunt(self, want, map_name, kind, landings, approach_name,
             budget) -> bool:
        d = self.d
        if self.has(want):
            log.info("== %s already CAUGHT -- skipping", want)
            return True
        nat = self.natdex(want)
        if not nat:
            log.error("!! %s has no dex entry -- skipping", want)
            return False
        log.info("-> %s on %s (%s), natdex %d, dex %d caught, balls %s",
                 want, map_name, kind, nat, len(self.caught()), self.balls())
        self.show_table(map_name, kind, want)
        deadline = time.time() + budget
        self.encounters = {}
        self.casts = self.hooks = 0
        approach = getattr(self, approach_name) if approach_name else None
        if kind == "land":
            self.block_foot_hazards(map_name)
        here = self.reach(map_name, landings)
        # ARRIVING IS NOT THE SAME AS ARRIVING SOMEWHERE USEFUL. Jagged Pass's
        # Fly-side door opens onto a walkable pocket with no grass in it, and
        # the first version of this leg happily "reached" that pocket and then
        # burned its budget on `goto`s the engine refused.
        if approach and (not here
                         or (kind == "land"
                             and not self.grass_cells(map_name))):
            log.info("   %s: nothing to pace from %s %s -- taking the "
                     "scripted approach", map_name, d.map_name(), d.pos())
            here = approach()
        if not here:
            log.error("!! could not reach %s (on %s %s)", map_name,
                      d.map_name(), d.pos())
            return False
        log.info("   on %s at %s (surfing=%s)", d.map_name(), d.pos(),
                 d.is_surfing())
        try:
            log.info("   sync_grid(%s): %d cells", map_name, d.sync_grid())
        except Exception as exc:  # noqa: BLE001
            log.info("   sync_grid: %s", str(exc)[:90])
        self.maybe_heal()
        if kind == "land":
            got = self.pace(want, map_name, deadline, approach=approach)
        else:
            got = self.fish_for(want, map_name, deadline)
        total = sum(self.encounters.values())
        log.info("<- %s: %s after %d encounters (%d casts, %d hooks) -- %s",
                 want, "CAUGHT" if got else "NOT CAUGHT", total, self.casts,
                 self.hooks, dict(sorted(self.encounters.items())))
        self.bank("after %s" % want)
        return got


def cold_read(state_path) -> tuple[int, dict]:
    """Re-open the save from disk and read the flags back. A mid-run counter
    is not evidence; the banked file is."""
    d = Driver(state_path)
    t = DexTarget(d.emu, d.names, d.consts, d.nav, spec=d.spec)
    caught, _seen = t.dex_flags(d.state)
    out = {}
    for entry in getattr(t, "entries", ()) or ():
        try:
            name = d.names.species(entry.species)
        except Exception:  # noqa: BLE001
            continue
        if name in ("SPOINK", "GRUMPIG", "ABSOL", "CORSOLA"):
            out[name] = entry.natdex in caught
    return len(caught), out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True,
                    help="save to MUTATE IN PLACE")
    ap.add_argument("--budget", type=float, default=900.0,
                    help="seconds per species, after arrival")
    ap.add_argument("--only", default="",
                    help="comma-separated species subset")
    ap.add_argument("--verify", action="store_true",
                    help="cold-read the flags and exit without driving")
    a = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(message)s")
    if "line3" in a.state or "milestone" in a.state:
        raise SystemExit("refusing to mutate a canonical state: %s" % a.state)
    if not Path(a.state).exists():
        raise SystemExit("no such state: %s" % a.state)

    if a.verify:
        count, flags = cold_read(a.state)
        log.info("COLD READ %s: dex %d, %s", a.state, count, flags)
        return 0

    only = {s.strip().upper() for s in a.only.split(",") if s.strip()}
    d = Driver(a.state)
    if d.at_title():
        d.resume_from_title()
    d.advance_scene(40000)
    trio = Trio(d, a.state)
    before = len(trio.caught())
    log.info("start: %s %s, dex %d caught", d.map_name(), d.pos(), before)

    todo = [h for h in HUNTS if not only or h[0] in only]
    pending = [h[0] for h in todo if not trio.has(h[0])]
    if not pending:
        log.info("nothing to do: %s already caught", [h[0] for h in todo])
        return 0
    log.info("pending: %s", pending)

    for (want, map_name, kind, landings, approach) in todo:
        try:
            trio.hunt(want, map_name, kind, landings, approach, a.budget)
        except Exception as exc:  # noqa: BLE001 - one species never kills the leg
            log.exception("!! %s raised %s", want, str(exc)[:150])
            try:
                trio.bank("after %s raised" % want)
            except Exception:  # noqa: BLE001
                pass

    trio.bank("leg end")
    after = len(trio.caught())
    log.info("done: dex %d -> %d (+%d)", before, after, after - before)
    for row in todo:
        want = row[0]
        log.info("   %-8s %s", want, "CAUGHT" if trio.has(want) else "missing")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
