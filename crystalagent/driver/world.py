"""Driver observation and world-state queries."""

import contextlib
import heapq
import inspect
import json
import logging
import random
import re
import sys
from collections import deque
from io import BytesIO
from pathlib import Path

from .. import hookevents, missables, paths
from ..battle import (Battle, BattleData, bag_item_index, bag_quantity,
                      cheapest_heal, goto_pocket)
from ..charmap import Charmap
from ..decide import DecisionRequired, TurnLog as _TurnLog, battle_frame as _decide_frame
from ..emu import Crystal, InputError, parse_sequence
from ..menus import Menus, battle_menu_up, dialog_press_safe, CURSORS
from ..names import Names
from ..nav import (COLL_PIT, CONN_NAME, HOPS, ICE, MapData, STEP, TrekNav,
                   WALKABLE, WARPS, WATER as _NAV_WATER, ICE as _NAV_ICE,
                   _CONN_LAND, _CONN_LETTER, _file_const, _tile_kind,
                   coord_events, mapgraph, render_map_view, scene_consts,
                   scene_vars, script_advances_scene, script_guards,
                   script_is_disruptive)
from ..schemas import validate_observe, validate_route
from ..state import (MONS_PER_BOX, SPRITE_WANDERERS, box_state, game_state,
                     live_sprites, status_line)
from ..symfile import Symbols

log = logging.getLogger("trek")


def _parse_event_flags(path):
    """constants/event_flags.asm -> {EVENT_NAME: bit index into wEventFlags}.

    One `const` walker for the whole harness (crystalagent.missables reads
    the same table to name the flag guarding each item gift); the local
    copy this replaced silently ignored `const_skip N`'s count."""
    return missables.event_bits(Path(path).parent.parent)


_MOVE_LENGTH = 7    # battle_constants.asm move_struct
_MOVE_PP_OFF = 5    # MOVE_PP
# wStatusFlags bit 0 (ram_constants.asm STATUSFLAGS_POKEDEX_F)
_STATUSFLAGS_POKEDEX_F = 1 << 0

_event_flag_names = None   # lazily parsed {EVENT_NAME: bit}
_move_base_pps = None      # lazily read [base PP per move id - 1]
_item_sources = None
_engine_flag_index = None



def _load_move_base_pps(rom_path, sym):
    """Base PP for every move, read straight from the ROM's Moves table."""
    bank, base = sym["Moves"]
    off = base if base < 0x4000 else bank * 0x4000 + (base - 0x4000)
    with open(rom_path, "rb") as f:
        rom = f.read()
    from crystalagent.names import NUM_MOVES
    return [rom[off + i * _MOVE_LENGTH + _MOVE_PP_OFF]
            for i in range(NUM_MOVES)]


class WorldMixin:
    """Owns Driver observation, map queries, flags, and world objects."""
    def pos(self):
        e = self.emu
        return (e.read_u8("wMapGroup"), e.read_u8("wMapNumber"),
                e.read_u8("wXCoord"), e.read_u8("wYCoord"))

    def map_name(self):
        g, n, _, _ = self.pos()
        return self.names.maps.get((g, n), f"?{g}:{n}")

    def _nav_resolve(self, name):
        nav = getattr(self, "nav", None)
        resolver = getattr(nav, "resolve", None)
        if callable(resolver):
            return resolver(name)
        if name in getattr(nav, "consts", {}):
            return name
        const = next((
            key for key, camel in getattr(nav, "camel", {}).items()
            if camel == name
        ), None)
        if const is not None:
            return const
        # Lightweight test/analysis drivers intentionally omit MapData.
        maps = getattr(getattr(self, "names", None), "maps", {})
        return name if name in maps.values() else None

    def _map_const(self):
        """Position's CONST_NAME: grid()/camel are keyed by CONST_NAME
        (nav.camel maps CONST_NAME -> CamelCase)."""
        name = self.map_name()
        return self._nav_resolve(name) or name

    def map_view(self, map_name=None):
        """ASCII view of the region reachable on foot/surf from the
        player's cell, with an ANNOTATION BLOCK under the grid naming the
        interesting cells by absolute coordinate.

        **This is art for humans. Decisions must come from the structured
        surface** -- `find_tiles`, `exits`, `tile_at`, `tiles_in`. The
        grid has a 5-column row gutter and a two-row x ruler, so answering
        "what is at x=15?" from it means counting characters in a
        monospace row, and a driving model got that wrong three times in
        one session (walked into an Ilex Forest wall 20x, put the Olivine
        pier warp at x=2 when it is x=3, and could only find the Vermilion
        Port Passage exit by grepping `warp_event`). The annotation block
        exists so the art can never disagree with the data.

        Pass a CamelCase or CONST map name to view another map from its
        origin cell (0,0)."""
        const = self._map_const()
        if map_name and map_name != self.map_name():
            const = self._nav_resolve(map_name)
            if const is None:
                raise SystemExit(f"unknown map {map_name!r}")
            art = render_map_view(self.nav, const, (0, 0))
            npcs = ()
        else:
            npcs = sorted(self.npc_cells())
            art = render_map_view(self.nav, const, self.pos()[2:],
                                  npcs=npcs,
                                  surf=bool(getattr(self.nav, "surf", False)))
        return art + "\n" + self._map_annotations(const, art, npcs)

    def _grid_of(self, map_name=None):
        """(grid, const) for a map name / the current map."""
        const = self._map_const() if map_name is None else (
            self._nav_resolve(map_name) or map_name
        )
        return self.nav.grid(const), const

    def tile_at(self, x, y, map_name=None):
        """Terrain word for ONE cell -- the same classifier
        `observe()['tiles']` uses, so the two can never disagree.
        'off-map' outside the grid."""
        grid, _ = self._grid_of(map_name)
        if not (0 <= y < len(grid) and 0 <= x < len(grid[0])):
            return "off-map"
        return _tile_kind(grid[y][x])

    def tiles_in(self, x0, y0, x1, y1, map_name=None):
        """A rectangle as ``{(x, y): kind}`` -- absolute coordinates, no
        gutters, no rulers, nothing to count. Bounds are inclusive and
        may be given in either order."""
        x0, x1 = sorted((int(x0), int(x1)))
        y0, y1 = sorted((int(y0), int(y1)))
        grid, _ = self._grid_of(map_name)
        out = {}
        for y in range(max(y0, 0), min(y1, len(grid) - 1) + 1):
            for x in range(max(x0, 0), min(x1, len(grid[0]) - 1) + 1):
                out[(x, y)] = _tile_kind(grid[y][x])
        return out

    def find_tiles(self, kind, map_name=None):
        """Every cell of a terrain `kind`, sorted -- the call that removes
        the character counting from the driving loop.

        `kind`: any word `tile_at` returns ('warp', 'water', 'grass',
        'floor', 'blocked', 'buoy', 'ice', 'pit', 'whirlpool',
        'waterfall', 'pc'), the FAMILY names 'ledge'/'sidewall' (which
        match 'ledge-up', 'sidewall-ur', ...), or 'npc' for live sprite
        cells.

        `find_tiles('warp')` answers off the collision bytes; `exits()`
        answers off the map's warp_events and edge connections. Both are
        useful and they are not the same question -- a warp tile with no
        event goes nowhere, and an event can sit on an odd byte."""
        want = str(kind).strip().lower()
        if want == "npc":
            if map_name not in (None, self.map_name(), self._map_const()):
                return []                 # live sprites are this map only
            return sorted(self.npc_cells())
        grid, _ = self._grid_of(map_name)
        out = []
        for y, row in enumerate(grid):
            for x, byte in enumerate(row):
                k = _tile_kind(byte)
                if k == want or (want in ("ledge", "sidewall")
                                 and k.startswith(want + "-")):
                    out.append((x, y))
        return sorted(out)

    def exits(self, map_name=None):
        """Every way OFF this map, with destinations -- the thing
        `grep warp_event maps/Foo.asm` was being used for.

        Warps come from the map's `warp_event`s and edge connections from
        its `connection` lines (the same nav data `travel` routes on):

            [{'kind': 'warp', 'x': 3, 'y': 14, 'to': 'VERMILION_PORT',
              'warp_id': 1},
             {'kind': 'connection', 'dir': 'north', 'to': 'ROUTE_6',
              'edge': 'y=0'}]
        """
        grid, const = self._grid_of(map_name)
        out = [{"kind": "warp", "x": x, "y": y, "to": dest, "warp_id": wid}
               for (x, y), (dest, wid) in
               sorted(self.nav.warps.get(const, {}).items())]
        edges = {"north": "y=0", "south": f"y={len(grid) - 1}",
                 "west": "x=0", "east": f"x={len(grid[0]) - 1}"}
        for d, (dest, _off) in sorted(
                self.nav.conns.get(const, {}).items()):
            out.append({"kind": "connection", "dir": d, "to": dest,
                        "edge": edges.get(d, "?")})
        return out

    def live_grid(self):
        """Collision grid decoded from the LIVE block map in WRAM -- the map
        the ENGINE is walking right now -- in nav.grid()'s shape.

        `wOverworldMapBlocks` holds the loaded map with a 3-block border on
        every side (stride = wMapWidth + 6, map block (bx,by) at
        (by+3)*stride + bx+3; the +1/+1 in GetMapScreenCoords is the screen
        anchor, not the map origin). Block ids go through the same tileset
        collision table nav uses, so any difference from nav.grid() is a
        real disagreement, not a decode artefact."""
        const = self._map_const()
        camel = self.nav.camel[const]
        w = self.emu.read_u8("wMapWidth")
        h = self.emu.read_u8("wMapHeight")
        stride = w + 6
        raw = self.emu.read("wOverworldMapBlocks", stride * (h + 6) + 8)
        table = self.nav._tileset_coll(self.nav.tileset[camel])
        grid = [[0x07] * (2 * w) for _ in range(2 * h)]
        for by in range(h):
            for bx in range(w):
                for i, c in enumerate(table[raw[(by + 3) * stride + bx + 3]]):
                    grid[by * 2 + i // 2][bx * 2 + i % 2] = c
        return grid

    def grid_drift(self):
        """Cells where the static decode disagrees with the live engine map:
        [(x, y, static_byte, live_byte), ...]. Empty is the normal answer.

        nav.grid() decodes maps/<Name>.blk, which is the map BEFORE any
        `changeblock` the map script fires (Burned Tower's basement ladder,
        Rocket base B3F's door to Giovanni, Goldenrod's underground doors).
        Those cells -- and only those -- can drift; conditional()/
        cell_kind()=='conditional' names them ahead of time. Audited across
        53 savestates of this run: 0 drift everywhere the events matched
        the default blockdata."""
        live, static = self.live_grid(), self.nav.grid(self._map_const())
        return [(x, y, static[y][x], live[y][x])
                for y in range(min(len(live), len(static)))
                for x in range(min(len(live[0]), len(static[0])))
                if live[y][x] != static[y][x]]

    def sync_grid(self):
        """Push every drifted cell into nav as a live override, so pathing
        uses the map the engine actually has. Returns the patched cells.

        Call it after any script that can open a door or drop a boulder --
        or just after arriving somewhere `conditional()` flags. Cheap
        (one WRAM read + a region-cache invalidation per cell) and
        idempotent; nav.clear_overrides() undoes it."""
        const = self._map_const()
        drift = self.grid_drift()
        for x, y, _static, live in drift:
            self.nav.set_cell(const, x, y, live)
        if drift:
            log.info(f"[sync_grid] {len(drift)} live cell(s) patched on "
                     f"{const}: " + " ".join(f"({x},{y})={l:#04x}"
                                             for x, y, _s, l in drift[:8]))
        return drift

    def _map_annotations(self, const, art, npcs=()):
        """The block printed under map_view's grid: every warp, NPC and
        water span the ART shows, by absolute coordinate.

        Built from the same calls a decision should use, so the picture
        and the data cannot drift apart. Cells outside the rendered window
        are counted, never silently dropped."""
        window = set()
        ox = self._view_origin(art)
        for line in art.splitlines():
            m = re.match(r"^(\s*\d+) (.*)$", line)
            if not m:
                continue
            y = int(m.group(1))
            for i, ch in enumerate(m.group(2)):
                if ch != " ":
                    window.add((ox + i, y))
        dest = {(e["x"], e["y"]): e["to"] for e in self.exits(const)
                if e["kind"] == "warp"}
        warps = self.find_tiles("warp", const)
        shown = [c for c in sorted(set(warps) | set(dest)) if c in window]
        hidden = len(set(warps) | set(dest)) - len(shown)
        lines = []
        if shown:
            lines.append("warps: " + "  ".join(
                f"({x},{y})" + (f"->{dest[(x, y)]}" if (x, y) in dest
                                else "->?")
                for x, y in shown)
                + (f"   (+{hidden} outside this view)" if hidden else ""))
        elif hidden:
            lines.append(f"warps: none in view (+{hidden} outside)")
        for e in self.exits(const):
            if e["kind"] == "connection":
                lines.append(f"edge:  {e['dir']} {e['edge']} -> {e['to']}")
        if npcs:
            lines.append("npcs:  " + " ".join(f"({x},{y})" for x, y in npcs))
        for line in self._offregion_lines(const):
            lines.append(line)
        for word in ("water", "grass"):
            cells = [c for c in self.find_tiles(word, const) if c in window]
            if cells:
                xs = [c[0] for c in cells]
                ys = [c[1] for c in cells]
                lines.append(f"{word}: rows {min(ys)}-{max(ys)}, "
                             f"x {min(xs)}-{max(xs)} ({len(cells)} cells)")
        try:
            drift = self.grid_drift() if const == self._map_const() else []
        except Exception as err:      # no live block map (other map, fake emu)
            log.debug(f"map_view: live grid unreadable ({err})")
            drift = []
        if drift:
            lines.append("DRIFT: the live engine map disagrees with the "
                         "decoded grid at " + "  ".join(
                             f"({x},{y}) {s:#04x}->{l:#04x}"
                             for x, y, s, l in drift[:6])
                         + (f"  (+{len(drift) - 6} more)"
                            if len(drift) > 6 else "")
                         + " -- a changeblock fired; call sync_grid()")
        lines.append("decide from find_tiles()/exits()/tiles_in(); "
                     "the grid above is for humans")
        return "\n".join(lines)

    def _offregion_lines(self, const):
        """One line per connected component the player cannot reach on this
        map, with its bounding box and the warp cells that open onto it.

        This is the fact that map_view's blanks used to swallow: Rocket
        base B3F's whole western wing (the rival and boss coord_events)
        hangs off two ladders that only B2F's left block can reach, and
        the render showed void. Sizes are cell counts, not blocks."""
        ids, count = self.nav.region_map(const)
        if count < 2:
            return []
        mine = set(self.nav.regions_at(const, *self.pos()[2:]))
        cells = {}
        for y, row in enumerate(ids):
            for x, rid in enumerate(row):
                if rid >= 0 and rid not in mine:
                    cells.setdefault(rid, []).append((x, y))
        if not cells:
            return []
        dest = {(e["x"], e["y"]): e["to"] for e in self.exits(const)
                if e["kind"] == "warp"}
        out = []
        for rid, pts in sorted(cells.items(),
                               key=lambda kv: -len(kv[1]))[:4]:
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            doors = [f"({x},{y})->{dest[(x, y)]}"
                     for (x, y) in sorted(dest)
                     if rid in self.nav.regions_at(const, x, y)]
            if not doors:
                # no warp touches it: the wall itself opens, via a
                # changeblock the map script fires on an event
                doors = [f"changeblock at {c}"
                         for c in sorted(self.nav.conditional(const))
                         if any(abs(c[0] - x) + abs(c[1] - y) <= 1
                                for x, y in pts)][:3]
            if not doors:
                if len(pts) < 8:
                    continue    # a decorative 2-cell island, not a wing
                doors = ["no warp and no changeblock found"]
            extra = len(doors) - 4      # a warp-pad maze (Ecruteak Gym has
            doors = doors[:4]           # 26 pads into one region) is noise
            out.append(
                f"offregion: {len(pts)} walkable cells at x {min(xs)}-"
                f"{max(xs)}, y {min(ys)}-{max(ys)} -- NOT reachable from "
                f"here; enter via " + "  ".join(doors)
                + (f"  (+{extra} more)" if extra > 0 else ""))
        return out

    @staticmethod
    def _view_origin(art):
        """The x of the leftmost rendered column, from map_view's header."""
        m = re.search(r"origin=\((\d+),", art.splitlines()[0])
        return int(m.group(1)) if m else 0

    def lead(self):
        s = game_state(self.emu, self.names)
        return s["party"][0] if s["party"] else None

    def status(self, missing=True):
        """One-line status. `missing=True` (default) appends a compact
        `missing: FLY(CIANWOOD_CITY 10,46) ...` fragment.

        This exists because a whole playthrough reached Champion without
        HM02 FLY -- it sat with Chuck's wife in Cianwood from the Storm
        Badge onward and NOTHING ever said so, so every journey of that
        run was on foot. status() is printed after almost every command,
        which makes it the one place a session cannot fail to look."""
        line = status_line(game_state(self.emu, self.names))
        if missing:
            try:
                frag = missables.status_fragment(self.missables())
            except Exception as exc:          # never break status()
                frag = f"missing: ? ({type(exc).__name__})"
            if frag:
                line += "  " + frag
        return line


    def item_sources(self):
        """Every place in the world an item can be obtained
        (crystalagent.missables.parse_item_sources), parsed once."""
        global _item_sources
        if _item_sources is None:
            _item_sources = missables.parse_item_sources(
                paths.REPO_ROOT, _file_const)
        return _item_sources

    def missables(self, kind="key"):
        """Items that are still out there, evaluated LIVE.

        `kind='key'` (default) is the subset that changes what the player
        can DO -- the game's own KEY_ITEM pocket plus the HMs;
        `kind='all'` is every giveitem/verbosegiveitem/itemball site in
        maps/. Each row is citable:

            {'item': 'HM_FLY', 'have': False, 'map': 'CIANWOOD_CITY',
             'x': 10, 'y': 46, 'event': 'EVENT_GOT_HM02_FLY',
             'source': 'maps/CianwoodCity.asm:100', ...}

        Obtained-ness comes from the guarding EVENT_GOT_* flag where the
        script has one, and from the bag otherwise."""
        def have_event(flag):
            try:
                return self._event_flag(flag)
            except ValueError:
                return None               # unknown flag: ask the bag
        return missables.missing_items(
            self.item_sources(), have_event=have_event, bag=self._bag(),
            repo=paths.REPO_ROOT, kind=kind)

    def field_moves(self):
        """``{'CUT': 'GATOR', 'FLY': None, ...}`` by current party."""
        party = game_state(self.emu, self.names)["party"]
        out = {}
        for tag, const in sorted(missables.hm_moves(paths.REPO_ROOT).items()):
            move_id = self.move_id(const)
            name = self.names.moves.get(
                move_id, const.replace("_", " ")
            )
            knower = None
            for mon in party:
                if any(mv["name"] == name for mv in mon.get("moves", [])):
                    knower = mon.get("nickname") or mon.get("name")
                    break
            out[name] = knower
        return out

    def dark_maps(self):
        """Map CONSTs that are pitch dark without FLASH (13 of them:
        ROCK_TUNNEL, DARK_CAVE, the WHIRL_ISLANDS, SILVER_CAVE_ROOM_1).

        Keyed on the map's PALETTE, not its tileset -- MOUNT_MORTAR and the
        ICE_PATH are TILESET_DARK_CAVE / ICE_PATH but PALETTE_NITE, and
        need no FLASH (see missables.DARK_PALETTE)."""
        out = set()
        for camel in missables.dark_map_names(paths.REPO_ROOT):
            const = self.nav.resolve(camel)
            if const:
                out.add(const)
        return out

    def needs_flash(self, map_name=None):
        """Is this map unusable without FLASH? Defaults to the current map."""
        return self._resolve_map(map_name) in self.dark_maps()

    def blocked_by(self):
        """Field-move gates I cannot currently pass, as
        ``{'FLASH': [MAP_CONST, ...]}``.

        `missables()` says what I do not HAVE; this says what that
        COSTS me. Live example that motivated it: a party with
        ``FLASH: None`` cannot use ROCK_TUNNEL (the Kanto shortcut),
        SILVER_CAVE_ROOM_1 (Red) or the WHIRL_ISLANDS (Lugia) -- three
        objectives gated by one uncollected HM."""
        out = {}
        if self.field_moves().get("FLASH") is None:
            dark = sorted(self.dark_maps())
            if dark:
                out["FLASH"] = dark
        return out

    WANDER_WAIT_CHUNK = 150      # frames between re-checks

    WANDER_WAIT_FRAMES = 600     # total patience per blocker cell

    def sprites(self):
        """LIVE overworld sprites from wObjectStructs (slot 0 = player).
        wMapObjects holds the map's STATIC definitions and never moves --
        reading it made pushed boulders look like they had reset."""
        return live_sprites(self.emu)

    def map_objects(self, map_name=None):
        """Every ``object_event`` this map DECLARES, read from its own
        source: ``[{'x','y','sprite','movement','script','event',
        'masked'}]``.

        The static counterpart of `sprites()`: those are live positions,
        these are the map's definitions -- which is what answers "where
        does this map keep its nurse/clerk" without hardcoding a layout.

        `masked` answers whether the object is actually THERE right now:
        the engine hides an object whose event flag is SET
        (`CheckObjectFlag`, engine/overworld/map_objects_2.asm:31-56 --
        flag set => masked), so a declared NPC can be absent and a
        declared item ball long gone. None means "no flag, or unreadable"
        -- an object with no flag is always present. Live positions still
        come from `sprites()`/`npc_cells()`, which only see the sprites
        the game has instantiated near the camera."""
        const = self._resolve_map(map_name) if map_name else self._map_const()
        camel = self.nav.camel.get(const, const)
        out = missables.parse_map_objects(
            Path(paths.REPO_ROOT, "maps", f"{camel}.asm"))
        for o in out:
            flag = o.get("event")
            masked = None
            if flag:
                try:
                    masked = bool(self._event_flag(flag))
                except Exception:
                    masked = None
            o["masked"] = masked
        return out

    def sprite_cell(self, sprite, map_name=None):
        """(x, y) of the first PRESENT object_event with sprite constant
        `sprite` ('SPRITE_NURSE', 'SPRITE_CLERK'), or None. Coordinates
        are walk cells -- the same space `pos()` and `talk_to` use.

        An object whose event flag is set is masked out by the engine and
        is not standing there at all, so it is skipped; a masked one is
        only returned when nothing else matches (better a stale guess
        than None for callers that just want the counter's layout)."""
        want = str(sprite).strip().upper()
        fallback = None
        for o in self.map_objects(map_name):
            if o["sprite"].upper() != want:
                continue
            if o.get("masked"):
                fallback = fallback or (o["x"], o["y"])
                continue
            return o["x"], o["y"]
        return fallback

    def npc_cells(self):
        """Cells occupied by live NPCs (walk-cell coords, player excluded).
        Degrades to empty when the struct table cannot be read, so nav
        keeps working on reduced fakes/odd states."""
        try:
            return {(s["map_x"], s["map_y"])
                    for s in self.sprites() if s["slot"]}
        except Exception:
            return set()

    BOULDER_MOVEMENT = 0x19

    def boulder_cells(self):
        """Cells holding a STRENGTH boulder right now.

        A boulder is an object_event like any other, so `npc_cells` counts
        it and nav calls it a stationary blocker -- which made Cianwood
        Gym's three-boulder puzzle read as "severs the only path" and even
        the savestate search refused to look (it will not search around an
        NPC). They are PUSHABLE: with STRENGTH in the party, a blocked
        step into one is a shove, not a wall."""
        try:
            return {(s["map_x"], s["map_y"]) for s in self.sprites()
                    if s["slot"] and s.get("movement") == self.BOULDER_MOVEMENT}
        except Exception:
            return set()

    def can_push(self):
        """True when a party member knows STRENGTH (the field move that
        makes a boulder cell walk-throughable, one shove at a time)."""
        try:
            return bool(self.field_moves().get("STRENGTH"))
        except Exception:
            return False

    def _blocker_kind(self, cell):
        """'wanderer' | 'stationary' | None for the sprite standing on
        `cell`. None means "cannot tell" (unreadable table, or nothing
        there any more) -- callers fall back to legacy handling."""
        try:
            live = self.sprites()
        except Exception:
            return None
        for s in live:
            if s["slot"] and (s["map_x"], s["map_y"]) == cell:
                return ("wanderer" if s["movement"] in SPRITE_WANDERERS
                        else "stationary")
        return None

    def _wait_out_wanderer(self, cell):
        """Idle in WANDER_WAIT_CHUNK slices until the sprite on `cell`
        steps off, up to WANDER_WAIT_FRAMES. True = cell is free (or no
        longer knowable, so the walk may as well try)."""
        waited = 0
        while waited < self.WANDER_WAIT_FRAMES:
            self.press(f".:{self.WANDER_WAIT_CHUNK}")
            waited += self.WANDER_WAIT_CHUNK
            try:
                busy = any(s["slot"] and (s["map_x"], s["map_y"]) == cell
                           for s in self.sprites())
            except Exception:
                return True
            if not busy:
                log.info(f"  wanderer left {cell} after {waited}f")
                return True
        return False

    def _sprites_obs(self):
        """observe()'s sprite list; empty when the table is unreadable."""
        try:
            return self.sprites()
        except Exception:
            return []

    def _event_flag(self, name):
        """True if event flag EVENT_<name> (or bare <name>) is set, read
        banked from wEventFlags. Symbol must exist in
        constants/event_flags.asm -- unknown names raise."""
        global _event_flag_names
        if _event_flag_names is None:
            _event_flag_names = _parse_event_flags(
                paths.REPO_ROOT / "constants" / "event_flags.asm")
        for key in (name, "EVENT_" + name):
            if key in _event_flag_names:
                bit = _event_flag_names[key]
                break
        else:
            raise ValueError(f"unknown event flag {name!r}")
        bank, addr = self.emu.sym["wEventFlags"]
        return bool(self.emu.read((bank, addr + bit // 8))[0]
                    >> (bit % 8) & 1)


    def engine_flag(self, name):
        """True if engine flag ENGINE_<name> (or bare <name>) is set.

        The index comes from constants/engine_flags.asm; the (address,
        mask) pair comes from the ROM's OWN assembled `EngineFlags` table
        (data/events/engine_flags.asm, 3 bytes per entry: little-endian
        WRAM address then the mask), so no bit constant is retyped here.
        All those addresses are WRAM bank 1. Unknown names raise."""
        global _engine_flag_index
        if _engine_flag_index is None:
            from crystalagent.asmconst import parse_const_defs
            _engine_flag_index = parse_const_defs(
                paths.REPO_ROOT / "constants" / "engine_flags.asm")
        table = _engine_flag_index
        for key in (name, "ENGINE_" + str(name)):
            if key in table:
                idx = table[key]
                break
        else:
            raise ValueError(f"unknown engine flag {name!r}")
        bank, addr = self.emu.sym["EngineFlags"]
        entry = self.emu.read((bank, addr + 3 * idx), 3)
        target = int.from_bytes(entry[:2], "little")
        return bool(self.emu.read((1, target))[0] & entry[2])

    def observe(self):
        """JSON-serializable full game snapshot (the serve.py contract)."""
        global _move_base_pps
        s = game_state(self.emu, self.names)
        loc = s["location"]
        if _move_base_pps is None:
            _move_base_pps = _load_move_base_pps(paths.ROM, self.emu.sym)
        party = []
        for mon in s["party"]:
            moves = []
            for move in mon["moves"]:
                move_id = self.move_id(move["name"])
                base = _move_base_pps[move_id - 1]
                ups = move["pp"] >> 6
                current = move["pp"] & 0x3F
                moves.append({
                    "name": move["name"],
                    "pp": current,
                    "max_pp": base + min(base // 5, 7) * ups,
                })
            party.append({
                "species": mon["name"],
                "nick": mon["nickname"],
                "level": mon["level"],
                "hp": mon["hp"],
                "max_hp": mon["max_hp"],
                "status": "+".join(mon["status"]) or None,
                "moves": moves,
                "egg": bool(mon.get("egg")),
            })
        flags = {}
        for const in (
            "GOT_MYSTERY_EGG_FROM_MR_POKEMON",
            "GAVE_MYSTERY_EGG_TO_ELM",
            "GOT_TOGEPI_EGG_FROM_ELMS_AIDE",
        ):
            flags[const] = self._event_flag(const)
        flags["POKEDEX"] = bool(
            self.emu.read_u8("wStatusFlags") & _STATUSFLAGS_POKEDEX_F)

        tiles = {}
        try:
            grid = self.nav.grid(loc["map"])
            cx, cy = loc["x"], loc["y"]
            tiles["here"] = _tile_kind(grid[cy][cx])
            for direction, (dx, dy) in STEP.items():
                nx, ny = cx + dx, cy + dy
                tiles[direction.lower()] = (
                    _tile_kind(grid[ny][nx])
                    if 0 <= ny < len(grid) and 0 <= nx < len(grid[0])
                    else "off-map"
                )
        except Exception:
            log.debug("observe: tile context unavailable", exc_info=True)

        try:
            sprites = live_sprites(self.emu)
        except Exception:
            log.debug("observe: sprite table unavailable", exc_info=True)
            sprites = []
        npcs = sorted([
            [sprite["map_x"], sprite["map_y"]]
            for sprite in sprites if sprite["slot"]
        ])
        obs = {
            "map": loc["map"],
            "group": loc["map_group"],
            "number": loc["map_number"],
            "x": loc["x"],
            "y": loc["y"],
            "tiles": tiles,
            "party": party,
            "bag": self._bag(),
            "money": s["player"]["money"],
            "badges": s["player"]["johto_badges"] + s["player"]["kanto_badges"],
            "flags": flags,
            "npcs": npcs,
            "sprites": sprites,
            "ui": {"textbox": self.textbox(), "battle": bool(s["battle"])},
            "frame": s["frame"],
        }
        if s["battle"]:
            try:
                enemy = Battle(self.emu, self.names, self.bdata).enemy()
                type_names = {value: name for name, value in self.bdata.types.items()}
                obs["enemy"] = {
                    "species": enemy["species"],
                    "name": enemy["name"],
                    "level": enemy["level"],
                    "hp": enemy["hp"],
                    "max_hp": enemy["max_hp"],
                    "types": [
                        type_names.get(value, str(value))
                        for value in enemy.get("types", [])
                    ],
                }
            except Exception:
                log.debug("observe: enemy frame unavailable", exc_info=True)
        return validate_observe(obs)
