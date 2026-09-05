"""Why can't I get there? Answered from the map data, in seconds.

Every gate this project has hit cost between thirty and sixty minutes of
hand diagnosis, and the shape was identical every time:

* Slateport's museum -- three Team Aqua grunts on the only door approach.
* Mauville's gym -- Wally and his uncle on the only door approach.
* Route 111 -- two breakable rocks in the only corridor.
* Route 117 -- a seam crossing that lands in a one-cell pocket.
* Dewford -- no road at all; the only exit is a boat.

Four of those five were an OBJECT, and in every case the pathfinder was
telling the truth and being disbelieved. The diagnosis is mechanical -- walk
the frontier of what is reachable, ask what sits on it, and look up what
clears each thing -- so it should not be done by hand.

This module does that. It is a diagnostic, not a planner: it says what is in
the way and what the game's own scripts say removes it, and leaves the
decision to the caller.
"""

from __future__ import annotations

import logging

log = logging.getLogger("pokeagent.blockers")

#: Map-object scripts that are scenery a field move removes.
FIELD_OBSTACLES = {
    "S_BreakableRock": "ROCK SMASH",
    "S_CuttableTree": "CUT",
    "S_PushableBoulder": "STRENGTH",
}


class Blocker:
    """One reason a road is shut, with what the game says opens it."""

    def __init__(self, kind, cell, detail, clears=None, map_name=None):
        self.kind = kind          # "obstacle" | "object" | "gate" | "no-seam"
        self.cell = cell
        self.detail = detail
        self.clears = clears      # the HM, flag or step that removes it
        self.map = map_name

    def __repr__(self):
        where = f"{self.map}{self.cell}" if self.cell else str(self.map)
        tail = f" -- needs {self.clears}" if self.clears else ""
        return f"<{self.kind} {where}: {self.detail}{tail}>"

    def as_dict(self):
        return {"kind": self.kind, "map": self.map, "cell": self.cell,
                "detail": self.detail, "clears": self.clears}


class Blockers:
    """Explain why a destination is unreachable from where the player is."""

    def __init__(self, driver):
        self.d = driver

    # ---- the frontier -----------------------------------------------------

    def reachable_maps(self, limit: int = 40) -> set:
        """Maps walkable from here without opening anything.

        Bounded, because "everywhere" is most of Hoenn once the roads are
        open and the answer stops being useful long before then.
        """
        d = self.d
        start = d.map_name()
        seen, frontier = {start}, [(start, d.pos())]
        while frontier and len(seen) < limit:
            here, cell = frontier.pop()
            try:
                exits = d.nav.usable_exits(here, cell)
            except Exception:  # noqa: BLE001 - an unreadable map is a dead end
                continue
            for edge in exits:
                landing = d.nav.exit_landing(here, edge)
                if landing is None:
                    continue
                dest, x, y = landing
                if dest in seen or x is None:
                    continue
                seen.add(dest)
                frontier.append((dest, (x, y)))
        return seen

    # ---- what is in the way -----------------------------------------------

    def obstacles_on(self, map_name) -> list:
        """Breakable rocks, cuttable trees and pushable boulders, live."""
        try:
            objects = self.d.nav.info(map_name).objects or []
        except Exception:  # noqa: BLE001
            return []
        want = {}
        for obj in objects:
            hm = FIELD_OBSTACLES.get(str(obj.get("script") or ""))
            if hm:
                want[(obj["x"], obj["y"])] = hm
        if not want:
            return []
        live = {(o["x"], o["y"]) for o in self._live_objects(map_name)}
        return [
            Blocker("obstacle", cell, f"{hm.title()} scenery", hm, map_name)
            for cell, hm in want.items() if cell in live
        ]

    def _live_objects(self, map_name):
        if map_name != self.d.map_name():
            # Only the current map has live positions; elsewhere the map file
            # is the best available answer and is usually right.
            try:
                return self.d.nav.info(map_name).objects or []
            except Exception:  # noqa: BLE001
                return []
        return [o for o in self.d.live_npcs() if not o["player"]]

    def door_blockers(self, map_name, cell) -> list:
        """People or scenery standing on the only approaches to a door.

        This is the shape that has cost the most time. A door tile is usually
        walled on three sides, so one object on the fourth is a sealed door,
        and `take_warp` reports the entirely accurate "no approach".
        """
        d = self.d
        out = []
        approaches = []
        for dx, dy in ((0, 1), (0, -1), (1, 0), (-1, 0)):
            neighbour = (cell[0] + dx, cell[1] + dy)
            tile = d.nav.cell(map_name, *neighbour)
            if tile is not None and tile.passable:
                approaches.append(neighbour)
        if not approaches:
            out.append(Blocker("no-seam", cell,
                               "every neighbour is wall", None, map_name))
            return out
        occupied = {(o["x"], o["y"]): o for o in self._live_objects(map_name)}
        standing = [a for a in approaches if a in occupied]
        if len(standing) == len(approaches):
            for spot in standing:
                obj = occupied[spot]
                script = str(obj.get("script") or "?")
                hm = FIELD_OBSTACLES.get(script)
                out.append(Blocker(
                    "obstacle" if hm else "object", spot,
                    f"{script} holds the only approach to {cell}",
                    hm or self._what_hides(map_name, obj), map_name))
        return out

    def _what_hides(self, map_name, obj):
        """The flag that removes an object, when the map declares one."""
        flag = obj.get("flag")
        return f"setflag {flag}" if flag and flag != "0" else None

    # ---- the answer -------------------------------------------------------

    def chokepoints(self, target, map_name=None) -> list:
        """Objects whose removal would open a route to `target`.

        The door-approach check misses these entirely, and that false negative
        is why the diagnostic said "no blockers found" for a road held shut by
        two Team Magma grunts. They were not on a door; they were standing in a
        corridor on the way to one.

        The test is causal rather than positional: a path exists with live
        objects ignored and does not with them marked, so remove each candidate
        in turn and see which one restores it. That names the object actually
        responsible instead of every object nearby.
        """
        d = self.d
        name = map_name or d.map_name()
        if name != d.map_name():
            return []
        here = d.pos()
        elevation = d.elevation()
        d.nav.blocked.pop(name, None)
        if d.nav.find_path(name, here, target, elevation) is None:
            return []                      # genuinely no route; not an object
        d._mark_npcs(name)
        marked = set(d.nav.blocked.get(name, ()))
        if not marked or d.nav.find_path(name, here, target, elevation) is not None:
            return []                      # objects are not the problem
        objects = {(o["x"], o["y"]): o for o in (d.nav.info(name).objects or [])}
        out = []
        for cell in sorted(marked):
            without = marked - {cell}
            d.nav.blocked[name] = set(without)
            if d.nav.find_path(name, here, target, elevation) is not None:
                obj = objects.get(cell, {})
                script = str(obj.get("script") or "unnamed object")
                hm = FIELD_OBSTACLES.get(script)
                out.append(Blocker(
                    "obstacle" if hm else "object", cell,
                    f"{script} blocks the only route to {target}",
                    hm or self._what_hides(name, obj), name))
        d.nav.blocked[name] = marked
        return out

    def to_map(self, dest_map) -> list:
        """Why `dest_map` cannot be reached from here."""
        d = self.d
        here = d.map_name()
        if dest_map == here:
            return []
        legs = None
        try:
            legs = d.nav.route_legs(here, d.pos(), dest_map)
        except Exception:  # noqa: BLE001
            pass
        if legs:
            return []

        out = []
        reachable = self.reachable_maps()
        if dest_map in reachable:
            # The map graph says it is next door, so the obstruction is local.
            out.extend(self.obstacles_on(here))
        else:
            out.append(Blocker(
                "no-seam", None,
                f"no walkable route from {here}; reachable maps are "
                f"{sorted(reachable)[:8]}", None, dest_map))
            out.extend(self.obstacles_on(here))
        return out

    def to_warp(self, cell, map_name=None) -> list:
        """Why a specific door cannot be entered.

        Two questions, not one: is something standing on the door's only
        approach, and is something standing between here and the door. The
        second was missing, and a road held by two Team Magma grunts in a
        corridor reported "no blockers found" 615 times.
        """
        name = map_name or self.d.map_name()
        cell = tuple(cell)
        found = self.door_blockers(name, cell)
        seen = {b.cell for b in found}
        for approach in ((cell[0], cell[1] + 1), (cell[0], cell[1] - 1),
                         (cell[0] + 1, cell[1]), (cell[0] - 1, cell[1])):
            tile = self.d.nav.cell(name, *approach)
            if tile is None or not tile.passable:
                continue
            for blocker in self.chokepoints(approach, name):
                if blocker.cell not in seen:
                    seen.add(blocker.cell)
                    found.append(blocker)
            break
        return found

    def explain(self, dest_map=None, warp=None) -> str:
        """One human-readable paragraph, for the log and the operator.

        Deliberately says when it found nothing: "no blockers found" and a
        silent empty list mean very different things at three in the morning.
        """
        found = self.to_warp(warp) if warp else self.to_map(dest_map)
        if not found:
            return f"no blockers found for {warp or dest_map}"
        lines = [f"{len(found)} blocker(s) for {warp or dest_map}:"]
        for blocker in found:
            lines.append(f"  {blocker!r}")
        usable = {b.clears for b in found if b.clears and b.clears.isupper()}
        if usable:
            known = self.d.field_moves()
            for hm in sorted(usable):
                who = known.get(hm)
                lines.append(
                    f"  {hm}: {'known by ' + who if who else 'NOBODY KNOWS IT'}")
        return "\n".join(lines)
