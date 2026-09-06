#!/usr/bin/env python
"""Catch SKITTY (Route 116 grass) and NOSEPASS (Granite Cave B2F, ROCK SMASH).

Two species, two completely different acquisition methods, and the second one
is the interesting half.

SKITTY is ordinary grass -- but it is a ONE-PERCENT PAIR. Route 116's land
table (pret/src/data/wild_encounters.json, `Route116_Sapphire`) puts SKITTY in
slots 10 and 11, and Gen 3's land slot weights are
``20,20,10,10,10,10,5,5,4,4,1,1``, so the two SKITTY slots are 2% of land
encounters between them. At the table's encounter_rate of 20 a grass step
triggers ``20*16/2880`` = 11.1% of the time, so a SKITTY costs on the order of
450 grass steps. That is a grind, not a puzzle: pace grass, and FLEE from
everything that is not the target rather than fight it. Fleeing is what makes
it finish -- a KO turn on a level-100 lead costs the same real time as a flee
turn plus the faint/exp/level-up scenes behind it.

NOSEPASS never appears in grass at all. Its only table in the entire game is
``GraniteCave_B2F.rock_smash_mons`` slot 1, which is rolled by
``ScrSpecial_RockSmashWildEncounter`` (pret/src/wild_encounter.c:518) from the
END of ``S_BreakableRock`` (pret/data/scripts/field_move_scripts.inc:58-96) --
i.e. it fires when a BREAKABLE ROCK OBJECT is destroyed, not when the player
walks. So the loop is: smash rocks. B2F ships exactly seven of them, at
(7,12), (5,14), (3,14), (2,16), (3,21), (4,22) and (6,22), and each one is
hidden behind ``FLAG_TEMP_11``..``FLAG_TEMP_17``.

TEMP flags are the whole trick. ``ClearTempFieldEventData``
(pret/src/event_data.c:35) wipes the temp flag block on every map load
(overworld.c:614,649), so **the rocks respawn every time the map is
re-entered**. B1F's warp at (29,13) lands on B2F's warp at (29,13) and vice
versa, so a re-entry is two steps, and seven fresh rocks are available again.
Per rock the odds are 11.1% (rate 20) * 30% (slot 1 under
``ChooseWildMonIndex``'s water/rock weights 60/30/5/4/1) = 3.3%, so a lap of
seven rocks is worth about 21% of a NOSEPASS and five laps is the expectation.

`Driver.smash_rock` is not used for the smash itself. It ends with
``advance_scene(60_000)`` and then reads `live_npcs()` to decide whether the
rock went away -- and the ONE case that matters here is the case where the
smash started a battle, which `advance_scene` now (correctly) refuses to touch.
So this drives the prompt itself and hands the battle to `fight()`.
"""

import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from pokeagent import dex as dexmod          # noqa: E402
from pokeagent.trek import Driver, TravelInterrupted  # noqa: E402

log = logging.getLogger("skitty_nosepass")

#: The B2F breakable rocks, in the order a single lap should visit them:
#: north cluster first (the arrival warp is east at (29,13)), then south.
#: Taken from pret/data/maps/GraniteCave_B2F/map.json's OBJ_EVENT_GFX_BREAKABLE_ROCK
#: entries, which carry FLAG_TEMP_11..FLAG_TEMP_17 in this same order.
B2F_ROCKS = [(7, 12), (5, 14), (3, 14), (2, 16), (3, 21), (4, 22), (6, 22)]

#: The two-step re-entry that respawns them. Both sides of the pair sit on the
#: same coordinate, which is a happy accident of the map data.
B2F_WARP = (29, 13)

CAVE = "GraniteCave_B2F"
CAVE_UP = "GraniteCave_B1F"


class Hunt:
    def __init__(self, driver, deadline=None):
        self.d = driver
        self.deadline = deadline
        self.target = dexmod.DexTarget(
            driver.emu, driver.names, driver.consts, driver.nav,
            spec=driver.spec,
        )
        #: Species we will spend a ball on. Names, upper case, because the
        #: battle frame reports the NAME (it is built for logs).
        self.wanted = {"SKITTY", "NOSEPASS"}
        self.caught = []
        self.encounters = 0
        self.smashed = 0
        self._baseline = self.caught_ids()
        # EVERY battle in this process, not just the ones this script starts.
        # See `policy` for what happened without it.
        driver.battle_policy = self.policy

    # ---- dex ------------------------------------------------------------

    def caught_ids(self) -> set:
        caught, _seen = self.target.dex_flags(self.d.state)
        return caught

    def has(self, name: str) -> bool:
        """Is `name` registered CAUGHT right now?

        Reads the flag, never the party: the party is full at six and a catch
        goes to a box, so "is it in the party" answers the wrong question.
        """
        try:
            sid = self.species_id(name)
            nat = self.target.natdex_of(sid) if hasattr(
                self.target, "natdex_of") else None
        except Exception:  # noqa: BLE001
            nat = None
        if nat is None:
            # Fall back to the entry objects, which carry both numbers.
            for entry in self.target.achievable:
                if self.name_of(entry.species) == name:
                    nat = getattr(entry, "natdex", None)
                    break
        if nat is None:
            return False
        return nat in self.caught_ids()

    def species_id(self, name: str) -> int:
        for entry in self.target.achievable:
            if self.name_of(entry.species) == name:
                return entry.species
        raise KeyError(name)

    def name_of(self, species_id) -> str:
        try:
            return str(self.d.names.species(species_id)).upper()
        except Exception:  # noqa: BLE001
            return ""

    def outstanding(self) -> set:
        return {n for n in self.wanted if not self.has(n)}

    # ---- battles --------------------------------------------------------

    def ball(self) -> str | None:
        """The best ball actually in the bag, or None when there is none.

        ULTRA first: both targets have a base catch rate of 255, so at full HP
        an ULTRA BALL is about 67% per throw and there is no reason to whittle
        a level-7 SKITTY with a level-100 lead -- one hit kills it and the
        catch is gone.
        """
        try:
            balls = self.d.state.bag().get("poke_balls") or {}
        except Exception:  # noqa: BLE001
            return None
        for name in ("ULTRA BALL", "GREAT BALL", "NET BALL", "TIMER BALL",
                     "DIVE BALL", "REPEAT BALL", "POKE BALL"):
            if int(balls.get(name, 0) or 0) > 0:
                return name
        # Anything at all beats nothing.
        for name, qty in balls.items():
            if int(qty or 0) > 0:
                return name
        return None

    def table_here(self) -> set:
        """Every species the CURRENT map can actually produce, by name.

        Cached per map: `for_map` parses the ROM's encounter tables and this is
        asked once per battle turn.
        """
        here = self.d.map_name()
        cache = getattr(self, "_tables", None)
        if cache is None:
            cache = self._tables = {}
        if here not in cache:
            names = set()
            try:
                for slot in self.target.wild.for_map(here):
                    name = self.name_of(slot.species)
                    if name:
                        names.add(name)
            except Exception:  # noqa: BLE001 - plenty of maps have no table
                names = set()
            cache[here] = names
        return cache[here]

    def policy(self, frame):
        """Ball the target, run from everything else.

        INSTALLED AS `Driver.battle_policy`, not merely passed to `fight()`.
        That distinction cost a NOSEPASS: the rock-smash roll landed one turn
        after `smash()` had already returned, `reenter()`'s `take_warp` walked
        into it, and `goto` resolves its own encounters by calling
        `Driver.fight()` with NO policy -- which falls back to tactics and
        SURFed it from 38 HP to 0 ("T1 attack:0 SURF#0 | NOSEPASS 38->0").
        `Driver.fight` documents `battle_policy` as the fix for exactly this
        (trek.py:3146-3160): install the decision once and every call site
        gets it, including the ones inside the walker.
        """
        enemy = frame.get("enemy") or {}
        species = str(enemy.get("species") or enemy.get("name") or "").upper()
        if not frame.get("wild"):
            return None                 # a trainer cannot be run from
        if species in self.wanted:
            ball = self.ball()
            if ball is not None:
                return ("ball", ball)
            # Out of balls: running keeps the encounter re-rollable, where a
            # KO spends it for nothing.
            log.info("no balls left for %s -- running instead of KOing it",
                     species)
            return "flee"
        # DO NOT FLEE ON A READ THIS MAP CANNOT PRODUCE. `frame["enemy"]` is
        # gBattleMons[1], and it is stale for a window after the battle
        # starts -- it can still describe the PREVIOUS encounter's mon. The
        # policy is only ever asked at the action menu (battle.py:1842), by
        # which point the block is populated, so this should never fire; but
        # "should never" is not worth a 2%-slot SKITTY. If the name is not in
        # this map's own table the read is not trustworthy, and the safe
        # action on an untrustworthy read is a BALL, never a RUN: a wasted
        # ULTRA BALL costs 1,200 of 965,199, while a wrong RUN spends an
        # encounter that took ~450 grass steps to find.
        table = self.table_here()
        if table and species not in table:
            ball = self.ball()
            log.info("%s is not in %s's table %s -- stale frame? balling it "
                     "rather than running", species or "<blank>",
                     self.d.map_name(), sorted(table))
            if ball is not None:
                return ("ball", ball)
        return "flee"

    def play_battle(self) -> str | None:
        """Resolve the battle in front of us. Returns the enemy's name.

        Waits for `battle_ready` before asking for a frame, for the reason
        collect.py documents at length: `battle_frame()` has no `wild` flag
        until the battle mon block is populated, and a plan built from an
        unsettled frame declines everything as a "trainer battle".
        """
        d = self.d
        for _ in range(120):
            if d.state.battle_ready():
                break
            d.emu.tick(20)
        if not d.in_battle():
            return None
        who = None
        try:
            frame = d.battle_frame()
            if frame:
                enemy = frame.get("enemy") or {}
                who = str(enemy.get("species") or enemy.get("name") or "")
        except Exception as exc:  # noqa: BLE001
            log.debug("frame: %s", str(exc)[:70])
        self.encounters += 1
        if who and who.upper() in self.wanted:
            log.info("[!] %s -- throwing %s (%d in the bag)", who, self.ball(),
                     sum((self.d.state.bag().get("poke_balls") or {}).values()))
        try:
            d.fight(policy=self.policy)
        except Exception as exc:  # noqa: BLE001 - never lose the run to one battle
            log.info("battle raised %s: %s", type(exc).__name__, str(exc)[:90])
        d.settle(200)
        self.poll_catches()
        return who

    def poll_catches(self) -> set:
        """Notice a catch against the run's own baseline and bank it.

        Polled rather than measured around one battle, because with
        `battle_policy` installed most encounters are resolved INSIDE
        `goto`/`travel` and never pass through `play_battle` at all.
        """
        now = self.caught_ids()
        gained = now - self._baseline
        if gained:
            self._baseline = now
            names = sorted(filter(None, (
                self.name_of(self.species_id_from_natdex(n)) for n in gained)))
            log.info("CAUGHT: dex now registers %s", names or sorted(gained))
            self.caught.extend(names)
            self.save()
        return gained

    def species_id_from_natdex(self, nat):
        for entry in self.target.achievable:
            if getattr(entry, "natdex", None) == nat:
                return entry.species
        return nat

    def clear_battle(self):
        """Play out anything that is live, so the caller starts from field."""
        for _ in range(4):
            if self.d.in_battle():
                self.play_battle()
                continue
            if self.d.scene_active():
                self.d.advance_scene(40000)
                continue
            break

    def walk_to(self, cell, budget_s, on_battle="fight") -> bool:
        """A goto with its own time bound, and the bound TAKEN BACK OFF after.

        `Driver._journey_deadline` is a plain attribute that `goto` reads if it
        is set (trek.py:857) -- it is not scoped to one call. Setting it and
        leaving it there poisoned every LATER walk in the process: once the
        45-second smash budget had elapsed, `take_warp`'s own internal `goto`
        to each of the four approach cells was refused instantly, and the
        re-entry that respawns the rocks reported "no approach to warp (29,13)
        on GraniteCave_B2F fired a map change" while standing 20 tiles away
        with nothing in the way. Arm it, use it, clear it.
        """
        d = self.d
        d._journey_deadline = time.time() + budget_s
        try:
            return bool(d.goto(*cell, on_battle=on_battle))
        finally:
            d._journey_deadline = None

    def save(self):
        d = self.d
        for _ in range(8):
            if not d.scene_active():
                break
            d.emu.run_sequence("B:4 .:30")
        if d.scene_active():
            d.advance_scene(40000)
        if d.scene_active() or d.in_battle():
            log.info("not saving: input is still owned")
            return
        try:
            d.save(d.state_path)
        except Exception as exc:  # noqa: BLE001
            log.info("save failed: %s", exc)

    def out_of_time(self) -> bool:
        return self.deadline is not None and time.time() >= self.deadline

    # ---- shopping -------------------------------------------------------

    def stock_balls(self, want=40) -> int:
        """Buy ULTRA BALLs at Mossdeep, the one shelf that is nothing but balls.

        MossdeepCity_Mart's list is ULTRA / NET / DIVE and nothing else
        (pret/data/maps/MossdeepCity_Mart/scripts.inc), so the cursor starts on
        the item we want. Money is 999,999 here; forty balls is 48,000.
        """
        d = self.d
        have = sum(int(v or 0) for v in
                   (d.state.bag().get("poke_balls") or {}).values())
        if have >= want:
            log.info("%d balls already -- not shopping", have)
            return have
        from pokeagent.mart import Mart

        mart = Mart(d)
        if not d.fly_to("MossdeepCity"):
            log.info("could not fly to Mossdeep: %s", d.last_fly_reason)
            return have
        self.clear_battle()
        if not d.travel("MossdeepCity_Mart", on_battle="fight", budget_s=180):
            log.info("could not reach the Mossdeep mart")
            return have
        cell = None
        info = d.nav.info("MossdeepCity_Mart")
        for obj in getattr(info, "objects", ()) or ():
            if "MART_EMPLOYEE" in str(obj.get("graphics_id", "")):
                cell = (int(obj["x"]), int(obj["y"]))
        if cell is None:
            log.info("no clerk on the mart map")
            return have
        d.talk_to(*cell)
        d.settle(120)
        for _ in range(4):
            if mart.is_open():
                break
            d.emu.run_sequence("A:4 .:40")
        if not mart.is_open():
            log.info("the clerk did not open a shop")
            d.emu.run_sequence("B:4 .:20 B:4 .:20")
            return have
        try:
            mart.buy("ULTRA BALL", want - have)
        except Exception as exc:  # noqa: BLE001
            log.info("buy raised: %s", str(exc)[:90])
        try:
            mart.leave()
        except Exception:  # noqa: BLE001
            pass
        now = sum(int(v or 0) for v in
                  (d.state.bag().get("poke_balls") or {}).values())
        log.info("balls: %d -> %d", have, now)
        self.save()
        return now

    # ---- SKITTY ---------------------------------------------------------

    def grass_cells(self, map_name):
        d = self.d
        cells = set(d.nav.find_tiles(map_name, "grass"))
        reach = set(d.nav.reachable(map_name, d.pos(), d.elevation()))
        px, py = d.pos()
        return sorted(cells & reach,
                      key=lambda c: abs(c[0] - px) + abs(c[1] - py))

    def hunt_skitty(self, budget_s=5400.0) -> bool:
        d = self.d
        if self.has("SKITTY"):
            log.info("SKITTY is already caught")
            return True
        deadline = time.time() + budget_s
        if self.deadline is not None:
            deadline = min(deadline, self.deadline)
        if d.map_name() != "Route116":
            if not d.fly_to("RustboroCity"):
                log.info("fly to Rustboro refused: %s", d.last_fly_reason)
            self.clear_battle()
            try:
                d.travel("Route116", on_battle="fight", budget_s=300)
            except Exception as exc:  # noqa: BLE001
                log.info("travel to Route116: %s", str(exc)[:90])
            self.clear_battle()
        if d.map_name() != "Route116":
            log.info("not on Route116 (on %s) -- cannot pace", d.map_name())
            return False
        cells = self.grass_cells("Route116")
        if not cells:
            log.info("no reachable grass on Route116")
            return False
        log.info("Route116: %d reachable grass cells, pacing for SKITTY",
                 len(cells))
        i = 0
        stalled = 0
        while time.time() < deadline and not self.has("SKITTY"):
            if stalled >= 8:
                log.info("pacing stalled: %s", d.last_goto_reason)
                break
            if d.in_battle():
                self.play_battle()
                continue
            if d.scene_active():
                d.advance_scene(40000)
                stalled += 1
                continue
            i += 1
            # Stride ACROSS the patch. Walking to the nearest grass cell
            # shuffles on one tile and the step counter that rolls encounters
            # barely moves; a long leg crosses many grass tiles per call.
            target = cells[(i * 7) % len(cells)]
            if target == d.pos():
                continue
            try:
                if self.walk_to(target, 60.0, on_battle="raise"):
                    stalled = 0
                else:
                    stalled += 1
            except TravelInterrupted:
                self.play_battle()
                d.advance_scene(20000)
                stalled = 0
            except Exception as exc:  # noqa: BLE001
                log.debug("pace: %s", str(exc)[:70])
                stalled += 1
            self.poll_catches()
            if i % 25 == 0:
                log.info("   %d legs, %d encounters, %d balls", i,
                         self.encounters,
                         sum((d.state.bag().get("poke_balls") or {}).values()))
        got = self.has("SKITTY")
        log.info("SKITTY: %s after %d encounters", "CAUGHT" if got else "no",
                 self.encounters)
        return got

    # ---- NOSEPASS -------------------------------------------------------

    def block_rocks(self):
        """Tell nav the rocks are walls, so a lap never routes THROUGH one.

        All seven stand-cells stay reachable with every rock blocked (207 of
        the region's 214 cells), so this costs nothing and removes the
        "planned a path over a rock, got refused, marked the cell" churn.
        """
        self.d.nav.blocked.setdefault(CAVE, set()).update(B2F_ROCKS)

    def smash(self, x, y) -> bool:
        """Smash the rock at (x, y), resolving any encounter it starts.

        Returns True when the prompt was accepted -- i.e. a roll happened.
        """
        d = self.d
        for dx, dy, facing in ((0, 1, "U"), (0, -1, "D"),
                               (1, 0, "L"), (-1, 0, "R")):
            stand = (x + dx, y + dy)
            cell = d.nav.cell(CAVE, *stand)
            if cell is None or not cell.passable or stand in B2F_ROCKS:
                continue
            if d.pos() != stand:
                try:
                    if not self.walk_to(stand, 45.0):
                        continue
                except TravelInterrupted:
                    self.play_battle()
                    continue
                except Exception as exc:  # noqa: BLE001
                    log.debug("approach %s: %s", stand, str(exc)[:60])
                    continue
            # Bump the rock to face it without moving, then press A. The
            # overworld A dispatches on the tile being FACED.
            d.step_dir(facing)
            d.emu.run_sequence("A:4 .:40")
            if "ROCK SMASH" not in (d.state.message() or "").upper():
                continue
            d.resolve_choice("YES")
            # WAIT PAST THE SCRIPT, NOT UNTIL IT GOES QUIET. `S_BreakableRock`
            # ends `applymovement` / `removeobject` / `special
            # ScrSpecial_RockSmashWildEncounter` / `waitstate`, and the frame
            # the encounter is decided on comes AFTER the smash animation has
            # released input. Breaking on the first quiet frame returned from
            # here while a battle was one tick away: the roll then landed in
            # the walker instead, where `goto` calls `Driver.fight()` and
            # (before `battle_policy` was installed) SURFed the NOSEPASS.
            # So require a QUIET WINDOW -- several hundred frames of neither
            # script nor battle -- before calling the roll a miss.
            quiet = 0
            for _ in range(400):
                if d.in_battle():
                    break
                quiet = 0 if d.scene_active() else quiet + 1
                if quiet >= 20:          # ~200 frames with nothing happening
                    break
                d.emu.tick(10)
            self.smashed += 1
            if d.in_battle():
                self.play_battle()
            else:
                d.advance_scene(20000)
            d.nav.blocked.get(CAVE, set()).discard((x, y))
            return True
        log.info("   could not face the rock at %s", (x, y))
        return False

    def reenter(self) -> bool:
        """Leave and re-enter B2F so ClearTempFieldEventData respawns the rocks."""
        d = self.d
        if not d.take_warp(*B2F_WARP, on_battle="fight"):
            log.info("   warp up refused: %s", d.last_warp_reason)
            return False
        self.clear_battle()
        if d.map_name() != CAVE_UP:
            log.info("   warp up landed on %s", d.map_name())
        if not d.take_warp(*B2F_WARP, on_battle="fight"):
            log.info("   warp down refused: %s", d.last_warp_reason)
            return False
        self.clear_battle()
        self.block_rocks()
        return d.map_name() == CAVE

    def hunt_nosepass(self, budget_s=5400.0, laps=200) -> bool:
        d = self.d
        if self.has("NOSEPASS"):
            log.info("NOSEPASS is already caught")
            return True
        deadline = time.time() + budget_s
        if self.deadline is not None:
            deadline = min(deadline, self.deadline)
        if d.map_name() != CAVE:
            if not d.fly_to("DewfordTown"):
                log.info("fly to Dewford refused: %s", d.last_fly_reason)
            self.clear_battle()
            try:
                d.travel(CAVE, on_battle="fight", budget_s=420)
            except Exception as exc:  # noqa: BLE001
                log.info("travel to %s: %s", CAVE, str(exc)[:90])
            self.clear_battle()
        if d.map_name() != CAVE:
            log.info("not on %s (on %s) -- cannot smash", CAVE, d.map_name())
            return False
        who = d.field_moves().get("ROCK SMASH")
        if not who:
            log.info("nobody in the party knows ROCK SMASH: %s",
                     d.field_moves())
            return False
        log.info("%s knows ROCK SMASH; smashing %d rocks per lap", who,
                 len(B2F_ROCKS))
        self.block_rocks()
        for lap in range(1, laps + 1):
            if time.time() >= deadline or self.has("NOSEPASS"):
                break
            hit = 0
            for (x, y) in B2F_ROCKS:
                if time.time() >= deadline or self.has("NOSEPASS"):
                    break
                if self.smash(x, y):
                    hit += 1
                self.poll_catches()
            log.info("lap %d: %d/%d rocks smashed (%d total, %d encounters)",
                     lap, hit, len(B2F_ROCKS), self.smashed, self.encounters)
            if self.has("NOSEPASS"):
                break
            if lap % 5 == 0:
                self.save()
            if not self.reenter():
                # Routing out and back is the fallback when the tight warp
                # pair refuses; it is slower but it reloads the map, which is
                # the only thing that matters.
                log.info("   re-entry via warp failed; routing instead")
                try:
                    d.travel(CAVE_UP, on_battle="fight", budget_s=240)
                    d.travel(CAVE, on_battle="fight", budget_s=240)
                except Exception as exc:  # noqa: BLE001
                    log.info("   re-entry travel: %s", str(exc)[:80])
                self.clear_battle()
                self.block_rocks()
                if d.map_name() != CAVE:
                    log.info("   lost the map (on %s) -- stopping", d.map_name())
                    break
        got = self.has("NOSEPASS")
        log.info("NOSEPASS: %s after %d rocks / %d encounters",
                 "CAUGHT" if got else "no", self.smashed, self.encounters)
        return got


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default="saves/sn.state")
    ap.add_argument("--out", default=None,
                    help="save here when done (default: --state)")
    ap.add_argument("--phase", default="all",
                    choices=("all", "shop", "skitty", "nosepass", "report"))
    ap.add_argument("--balls", type=int, default=40)
    ap.add_argument("--nosepass-budget", type=float, default=4200.0)
    ap.add_argument("--skitty-budget", type=float, default=7200.0)
    ap.add_argument("--budget", type=float, default=None,
                    help="overall wall-clock seconds")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO, stream=sys.stdout,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    if "line3" in args.state:
        raise SystemExit("refusing to touch the canonical line3 save")

    d = Driver(args.state)
    deadline = time.time() + args.budget if args.budget else None
    hunt = Hunt(d, deadline=deadline)
    log.info("booted on %s at %s %s; dex %d caught", args.state, d.map_name(),
             d.pos(), len(hunt.caught_ids()))
    log.info("outstanding: %s", sorted(hunt.outstanding()))

    if args.phase == "report":
        for name in sorted(hunt.wanted):
            log.info("%s caught=%s", name, hunt.has(name))
        return 0
    if args.phase in ("all", "shop"):
        hunt.stock_balls(args.balls)
    if args.phase in ("all", "nosepass"):
        hunt.hunt_nosepass(args.nosepass_budget)
    if args.phase in ("all", "skitty"):
        hunt.hunt_skitty(args.skitty_budget)

    hunt.save()
    out = args.out
    if out and out != args.state:
        hunt.clear_battle()
        try:
            d.save(out)
            log.info("banked %s", out)
        except Exception as exc:  # noqa: BLE001
            log.info("could not bank %s: %s", out, exc)
    log.info("done: dex %d caught; this run added %s", len(hunt.caught_ids()),
             hunt.caught or "nothing")
    for name in sorted(hunt.wanted):
        log.info("   %s caught=%s", name, hunt.has(name))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
