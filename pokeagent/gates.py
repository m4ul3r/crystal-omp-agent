"""Why can't I leave this way, and what opens it?

Routes in this game are closed by `coord_event`s: a cell that, while some
variable still holds some value, runs a script that pushes you back. The
harness could see the wall but not the reason, so a blocked journey reported
"could not cross the L seam to Route104" and left it there. That sent a whole
session grepping the decompilation by hand -- twice, for two different towns:

* Route 102 was shut because `VAR_ROUTE102_ACCESSIBLE` was 0, which
  `OldaleTown_OnTransition` keeps at 0 until `FLAG_ADVENTURE_STARTED` is set,
  which happens when Birch hands over the Pokedex, which needs the rival
  beaten on Route 103.
* Route 104 was shut because `VAR_PETALBURG_STATE` was 0.

Every one of those facts is IN the repository. This module reads them:

* which coord_event guards a cell, and whether it is live right now;
* every place in the game's own scripts that writes the variable it tests,
  so "what would open this" is answered by citation rather than by memory.

Nothing here is a walkthrough. It is the map data plus grep, which is why it
cannot go stale against the ROM.
"""

from __future__ import annotations

import functools
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from . import paths

log = logging.getLogger(__name__)

_SETVAR = re.compile(r"^\s*setvar\s+(VAR_[A-Z0-9_]+)\s*,\s*(\w+)", re.M)
_SETFLAG = re.compile(r"^\s*setflag\s+(FLAG_[A-Z0-9_]+)", re.M)


@dataclass(frozen=True)
class Gate:
    """One coord_event that can close a path."""

    map_name: str
    x: int
    y: int
    var: str | None
    var_value: int | None
    script: str

    def describe(self) -> str:
        if self.var is None:
            return f"{self.map_name} ({self.x},{self.y}) runs {self.script}"
        return (
            f"{self.map_name} ({self.x},{self.y}) is gated on "
            f"{self.var}=={self.var_value} -> {self.script}"
        )


@dataclass(frozen=True)
class Setter:
    """A place in the game's scripts that writes a variable or flag."""

    path: str
    line: int
    value: str | None

    def cite(self) -> str:
        where = f"{self.path}:{self.line}"
        return f"{where} (= {self.value})" if self.value is not None else where


@functools.lru_cache(maxsize=1)
def _map_dirs() -> tuple[Path, ...]:
    root = paths.MAPS
    if not root.exists():
        return ()
    return tuple(sorted(p for p in root.iterdir() if p.is_dir()))


@functools.lru_cache(maxsize=None)
def gates_for_map(map_name: str) -> tuple[Gate, ...]:
    """Every coord_event on a map, as Gates. Empty when the map has none."""
    path = paths.MAPS / map_name / "map.json"
    if not path.exists():
        return ()
    try:
        j = json.loads(path.read_text())
    except Exception as err:  # noqa: BLE001
        log.debug("unreadable map json for %s: %s", map_name, err)
        return ()
    out = []
    for c in j.get("coord_events") or ():
        # Only a SCRIPT can refuse to let the player past. Route 113's ash is
        # nineteen `"type": "weather"` coord_events with no script at all, and
        # treating them as gates blocked the road to Fallarbor so hard that
        # the landing from Route 111 shrank from 777 walkable cells to 16 --
        # the town became unroutable and badge 4 stalled behind a rain effect.
        if (c.get("type") or "trigger") != "trigger":
            continue
        script = c.get("script") or ""
        if not script:
            continue
        var = c.get("var") or None
        raw = c.get("var_value")
        try:
            value = int(raw) if raw is not None else None
        except (TypeError, ValueError):
            value = None
        out.append(
            Gate(map_name, int(c["x"]), int(c["y"]), var, value, script)
        )
    return tuple(out)


@functools.lru_cache(maxsize=None)
def setters(name: str) -> tuple[Setter, ...]:
    """Everywhere the game's own map scripts write this var or flag.

    This is the "what would open it" half. A var with no setter anywhere is a
    genuine dead end and worth saying so; a var with one setter names the exact
    script that advances the story.
    """
    pattern = _SETFLAG if name.startswith("FLAG_") else _SETVAR
    out = []
    for d in _map_dirs():
        script = d / "scripts.inc"
        if not script.exists():
            continue
        try:
            text = script.read_text(errors="replace")
        except Exception:  # noqa: BLE001
            continue
        if name not in text:
            continue
        for m in pattern.finditer(text):
            if m.group(1) != name:
                continue
            line = text.count("\n", 0, m.start()) + 1
            value = m.group(2) if pattern is _SETVAR else None
            rel = str(script.relative_to(paths.PRET)) if _under_pret(script) else str(script)
            out.append(Setter(rel, line, value))
    return tuple(out)


def _under_pret(path: Path) -> bool:
    try:
        path.relative_to(paths.PRET)
        return True
    except ValueError:
        return False


_LABEL = re.compile(r"^(\w+)::")
_JUMP = re.compile(r"^\s*(?:goto|call)(?:_if_eq|_if_ne|_if_lt|_if_gt|_if_le|_if_ge|_if_set|_if_unset)?\s+(?:\w+,\s*)?(\w+)")
_PLAYER_MOVE = re.compile(r"applymovement\s+(?:LOCALID_PLAYER|255|0xFF)\s*,\s*(\w+)")
#: `checkitem ITEM_X, 1` -- a gate whose guard is the BAG rather than a var.
_ITEM_GUARD = re.compile(r"checkitem\s+(ITEM_\w+)")


@functools.lru_cache(maxsize=None)
def _script_bodies(map_name: str) -> dict:
    """Every label in a map's scripts.inc mapped to its own lines."""
    path = paths.MAPS / map_name / "scripts.inc"
    if not path.exists():
        return {}
    out, label = {}, None
    for line in path.read_text().splitlines():
        m = _LABEL.match(line)
        if m:
            label = m.group(1)
            out[label] = []
            continue
        if label is not None:
            out[label].append(line)
    return out


@functools.lru_cache(maxsize=None)
def displaces_player(map_name: str, script: str) -> bool:
    """Does this script WALK THE PLAYER, following its own goto/call chain?

    This is the difference between a wall and a scene, and getting it wrong
    stops a run dead in either direction.

    Route 111's desert IS a wall: with no GO-GOGGLES it prints a message and
    `applymovement LOCALID_PLAYER, Route111_Movement_1501B4` -- a movement
    that carries the player back out. Pathing through it walks at sand
    forever.

    Route 119's rival ambush is NOT a wall: it fights you
    (`trainerbattle_no_intro TRAINER_MAY_6`) and releases. Its only player
    movement is `Common_Movement_WalkInPlaceFastestDown`, which turns the
    player on the spot and moves them nowhere. Marking it impassable severed
    Route 119 at a two-cell corridor, and since the north half holds the
    Weather Institute -- the run's own objective -- every plan collapsed to
    "no walkable route" while the player stood still.

    Walking in place is therefore explicitly NOT displacement. The chain is
    followed because the desert's push-back sits two `goto`s from the cell.
    """
    bodies = _script_bodies(map_name)
    seen, queue = set(), [script]
    while queue:
        label = queue.pop()
        if label in seen or label not in bodies:
            continue
        seen.add(label)
        for line in bodies[label]:
            mv = _PLAYER_MOVE.search(line)
            if mv and "WalkInPlace" not in mv.group(1):
                return True
            j = _JUMP.match(line)
            if j:
                queue.append(j.group(1))
    return False


@functools.lru_cache(maxsize=None)
def required_item(map_name: str, script: str) -> str | None:
    """The ITEM_ constant this gate checks for, if it checks for one.

    Some gates are guarded by the bag, not by a var, and `Gate.var` is None
    for those -- which `is_closed` reads as "unconditional", i.e. shut
    forever. Route 111's desert is exactly that: its script is
    `checkitem ITEM_GO_GOGGLES` and, without them, a message and a
    push-back movement (data/maps/Route111/scripts.inc:162-181).

    Cost of not reading it: nav marked all ten desert-entrance cells
    impassable with the goggles sitting in the bag, which severed Route 111
    in half. Measured from (13,138): 762 cells reachable, y stopping at
    exactly 61, and every fossil cell answering in_reach=False. Clearing
    them took the same fill to 2229 cells with both fossils in reach.

    The chain is followed for the same reason `displaces_player` follows it:
    the desert's check sits two `goto`s from the trigger cell.
    """
    bodies = _script_bodies(map_name)
    seen, queue = set(), [script]
    while queue:
        label = queue.pop()
        if label in seen or label not in bodies:
            continue
        seen.add(label)
        for line in bodies[label]:
            hit = _ITEM_GUARD.search(line)
            if hit:
                return hit.group(1)
            j = _JUMP.match(line)
            if j:
                queue.append(j.group(1))
    return None


class GateReader:
    """Gates evaluated against the LIVE game, so 'blocked' means now."""

    def __init__(self, state):
        self.state = state

    def value(self, var: str) -> int | None:
        try:
            return int(self.state.var(var))
        except Exception:  # noqa: BLE001
            return None

    def is_closed(self, gate: Gate) -> bool:
        """True when this gate's condition currently holds.

        A gate with no variable is unconditional: it always runs, which is why
        Route 32's push-back in the predecessor project blocked forever.
        """
        if gate.var is None:
            return True
        live = self.value(gate.var)
        return live is not None and live == gate.var_value

    def closed_gates(self, map_name: str) -> list[Gate]:
        """Gates whose condition holds AND that actually refuse passage.

        A satisfied condition is not a wall by itself. Callers use this to
        mark cells unwalkable, so a scene that merely fires -- a rival
        battle, a cutscene, a message -- must not appear here: blocking it
        severs the map at the trigger cell.
        """
        return [g for g in gates_for_map(map_name)
                if self.is_closed(g) and self.blocks(g)]

    def scenes(self, map_name: str) -> list[Gate]:
        """Live gates that fire but let the player through."""
        return [g for g in gates_for_map(map_name)
                if self.is_closed(g) and not self.blocks(g)]

    def blocks(self, gate: Gate) -> bool:
        """Does this gate push the player back RIGHT NOW?

        Two questions, not one: does the script displace the player at all
        (`displaces_player`), and is its guard currently satisfied. A gate
        guarded by `checkitem` stops being a wall the moment the item is in
        the bag, and treating it as permanent is what walled off Route 111's
        desert -- and with it both fossils -- while the GO-GOGGLES sat in the
        Key Items pocket.
        """
        try:
            if not displaces_player(gate.map_name, gate.script):
                return False
        except Exception:  # noqa: BLE001 - unreadable script: assume passable
            return False
        try:
            want = required_item(gate.map_name, gate.script)
        except Exception:  # noqa: BLE001
            want = None
        if want and self.holds(want):
            return False
        return True

    def holds(self, item_const: str) -> bool:
        """Is this ITEM_ constant in the bag? Never raises."""
        try:
            item_id = self.state.consts.items.get(item_const)
            if item_id is None:
                return False
            name = self.state.names.item(item_id)
            return any(name in pocket for pocket in self.state.bag().values())
        except Exception:  # noqa: BLE001 - a guard read must not stop routing
            return False

    def gate_at(self, map_name: str, x: int, y: int) -> Gate | None:
        for g in gates_for_map(map_name):
            if (g.x, g.y) == (x, y) and self.is_closed(g):
                return g
        return None

    def near(self, map_name: str, x: int, y: int, radius: int = 2) -> list[Gate]:
        """Live gates within `radius` of a cell.

        A seam crossing fails a step or two short of the trigger cell, so an
        exact match misses the gate that actually stopped the walk.
        """
        out = []
        for g in self.closed_gates(map_name):
            if abs(g.x - x) <= radius and abs(g.y - y) <= radius:
                out.append(g)
        return out

    def explain(self, map_name: str, x: int, y: int, radius: int = 3) -> str:
        """One line naming the gate and what advances it, or "" if none.

        Meant to be appended to a movement failure, so the error says why
        instead of only where.
        """
        gates = self.near(map_name, x, y, radius)
        if not gates:
            return ""
        # One line per VARIABLE, not per cell. A town blocks its exit with a
        # coord_event on every tile of the gap -- Petalburg uses four, all
        # testing VAR_PETALBURG_STATE -- and repeating the same explanation
        # four times buries it.
        by_var: dict[str | None, list[Gate]] = {}
        for g in gates:
            by_var.setdefault(g.var, []).append(g)
        parts = []
        for var, group in by_var.items():
            cells = ",".join(f"({g.x},{g.y})" for g in group[:4])
            if var is None:
                parts.append(f"{map_name} {cells} runs {group[0].script}")
                continue
            bit = (
                f"{map_name} {cells} gated on {var}=={group[0].var_value} "
                f"-> {group[0].script}"
            )
            srcs = setters(var)
            if srcs:
                bit += "; advanced at " + ", ".join(s.cite() for s in srcs[:3])
            else:
                bit += "; nothing in the scripts sets it"
            parts.append(bit)
        return " | ".join(parts)
