"""Driver movement, routing, travel, exploration, and NPC interaction."""

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

DIRS = {"U": "UP", "D": "DOWN", "L": "LEFT", "R": "RIGHT"}

class TravelError(RuntimeError):
    """travel(): a transition landed somewhere the plan didn't expect."""


class NavigationMixin:
    """Owns Driver movement, routing, travel, and NPC approach behavior."""
    @contextlib.contextmanager
    def _money_watch(self, where):
        """Log any money change across a MOVEMENT call.

        Navigation must never spend money. It did once: an A-mash beside a
        Poke Mart clerk bought ¥1200 of ESCAPE ROPEs one 200¥ press at a
        time, and nothing noticed until the wallet was read hours later
        (AGENTS.md gotcha 13). Clerk identity does not exist at runtime --
        object_event sprite ids are not in WRAM -- so watch the symptom
        instead: the wallet, around every movement entry point. Only the
        OUTER call is wrapped, so a purchase during a nested dialog drain
        is reported once, with the map and cell it happened on.

        Only a DECREASE warns. Trainer winnings arrive mid-walk all the
        time and `MONEY +216 ... movement must never spend money` was a
        false alarm that trained the reader to ignore the line; the delta
        is still recorded either way.
        """
        try:
            before = self.emu.read_be("wMoney", 3)
        except Exception:            # duck-typed fakes without a wallet
            yield
            return
        try:
            yield
        finally:
            after = self.emu.read_be("wMoney", 3)
            if after != before:
                self.last_money_delta = after - before
            if after < before:
                log.warning(
                    f"  MONEY {after - before:+d} (now {after}) during "
                    f"{where} at {self.map_name()} {self.pos()[2:]} -- "
                    f"movement must never SPEND money")

    def settle(self, quiet=3, spacing=20, max_frames=900):
        """Wait until map + position stop changing: door/cutscene warps
        finish asynchronously a beat AFTER the step that triggered them,
        so anything acting on the map right after a step must settle."""
        last, still = None, 0
        f0 = self.emu.frame
        while self.emu.frame - f0 < max_frames:
            if self.battle():
                return
            cur = self.pos()
            if cur == last:
                still += spacing
                if still >= quiet * spacing:
                    return
            else:
                last, still = cur, 0
            self.press(f".:{spacing}")

    def _grid_miss(self, what, exc):
        """A cell question the decoded map cannot answer.

        IndexError is ordinary (a coordinate one past the map edge); a
        KeyError means this map has no decoded grid at all, which silently
        turns every door on it into "not a warp cell" -- so say so."""
        self.last_step_reason = f"{what}: {type(exc).__name__}: {exc}"
        if isinstance(exc, KeyError):
            log.info(f"  no decoded grid for {self.map_name()} ({what})")
        return False

    def _is_warp_cell(self, x, y):
        try:
            grid = self.nav.grid(self.map_name())
            return grid[y][x] in WARPS
        except (KeyError, IndexError) as exc:
            return self._grid_miss(f"warp-cell({x},{y})", exc)

    def step_hold(self, mv, hold=80):
        """Hold the direction through the whole step AND the map
        transition. Door warps only fire if the key is still down when
        the step completes -- step_dir's early release skips them."""
        before = self.pos()
        self.emu.py.button_press(DIRS[mv].lower())
        self.emu.tick(hold)
        self.emu.py.button_release(DIRS[mv].lower())
        self.emu.tick(2)
        self.settle(max_frames=400)
        if self.battle():
            return "battle"
        now = self.pos()
        if now[:2] != before[:2]:
            return "warp"
        return "moved" if now != before else "blocked"

    def _is_water_cell(self, x, y):
        try:
            grid = self.nav.grid(self.map_name())
            return grid[y][x] in _NAV_WATER
        except (KeyError, IndexError) as exc:
            return self._grid_miss(f"water-cell({x},{y})", exc)

    def _mount_surf(self, mv):
        """Face the water and start surfing: walking into water does NOT
        prompt in GSC -- you must face it and press A ('The water is
        calm... SURF?' -> YES). Ends riding ON the water cell.

        Verified by EITHER wPlayerState==PLAYER_SURF or the avatar having
        actually moved onto the target cell: at a map-edge seam (New Bark
        -> Route 27) the mount slid us onto the water and still reported
        'blocked' off the state byte alone, so callers hand-rolled raw
        presses to cross."""
        before = self.pos()
        x, y = before[2:]
        dx, dy = STEP[mv]
        target = (x + dx, y + dy)
        self.step_dir(mv)              # blocked step = turn toward water
        for _ in range(10):
            s = "".join(self.emu.screen_text()).upper()
            if "YES" in s and "NO" in s:
                break
            self.press("A:4 .:30")
        else:
            return "blocked"
        self.press("A:5 .:40")         # YES
        self.settle(max_frames=600)    # mount animation slides onto water
        now = self.pos()
        if now[:2] != before[:2]:
            return "warp"              # seam crossing rode us to the next map
        if self.emu.read_u8("wPlayerState") == 4 or now[2:] == target:
            return "moved"
        return "blocked"

    def _step(self, mv):
        """step_dir, but switch to a held step when the target cell is a
        warp tile so doors actually trigger, and to the surf-mount flow
        when stepping from land onto water."""
        x, y = self.pos()[2:]
        dx, dy = STEP[mv]
        if self._is_warp_cell(x + dx, y + dy):
            r = self.step_hold(mv)
            if r != "moved" and r != "warp":
                r = self._step_warp_tap(mv)
            return r
        if self.nav.surf and self._is_water_cell(x + dx, y + dy) and \
                self.emu.read_u8("wPlayerState") != 4:
            return self._mount_surf(mv)
        return self.step_dir(mv)

    def _step_warp_tap(self, mv):
        """COLL_STAIRCASE tiles (CheckWarpFacingDown, tile_events.asm:35)
        push the player back OFF the tile if the key is still held ~60+
        frames after arrival -- long holds never warp. Tap-and-release at
        varying durations shifts the frame phase until the stop-on-tile
        lands inside the warp check."""
        button = DIRS[mv].lower()
        for hold in (56, 44, 64, 36, 72, 52):
            before = self.pos()
            self.emu.py.button_press(button)
            self.emu.tick(hold)
            self.emu.py.button_release(button)
            self.emu.tick(2)
            self.settle(max_frames=400)
            if self.battle():
                return "battle"
            now = self.pos()
            if now[:2] != before[:2]:
                return "warp"
            if now != before:
                return "moved"
        return "blocked"

    def _held_warp_entry(self, st):
        """Gotcha 12 last resort for a door warp a plain transition step
        crossed without firing: re-approach along the warp tile's
        row/column and drive onto it -- held first when adjacent (doors
        need the key down through the step), then _step_warp_tap's
        phase-shifted taps (staircase tiles bounce held keys back off).
        Multi-warp door rows (Sprout Tower 1F's double door) make a held
        glide cross BOTH tiles without firing; walking back tap-by-tap
        lands on one. Returns 'warp' | 'battle' | None (couldn't fire)."""
        tx, ty = st["cell"]
        px, py = self.pos()[2:]
        dist = abs(px - tx) + abs(py - ty)
        if dist == 0 or dist > 3 or (px != tx and py != ty):
            return None         # on the tile, too far, or off its axes
        if px != tx:
            mv = "R" if tx > px else "L"
        else:
            mv = "D" if ty > py else "U"
        log.info(f"  held-entry fallback: {mv} onto warp {(tx, ty)} "
              f"from {(px, py)}")
        if dist == 1:
            r = self.step_hold(mv)
            if r in ("warp", "battle"):
                return r
        r = self._step_warp_tap(mv)
        return r if r in ("warp", "battle") else None

    def _warp_fail(self, reason):
        self.last_warp_reason = reason
        log.warning(f"  take_warp: {reason}")
        return False

    def _same_map_landing(self, target):
        """Where a SAME-MAP warp at `target` lands, or None when the warp
        leaves the map (or is not a warp here).

        A warp_event's destination is (map, warp id), and warp ids are
        1-based positions in that map's own `def_warp_events` -- the order
        nav.warps preserves. Victory Road's `warp_event 13, 31,
        VICTORY_ROAD, 5` therefore lands on the 5th entry, (13,17)."""
        try:
            const = self._map_const()
            table = self.nav.warps.get(const, {})
        except Exception:
            return None
        dest = table.get(tuple(target))
        if not dest or dest[0] != const:
            return None
        cells = list(table)
        idx = int(dest[1]) - 1
        return cells[idx] if 0 <= idx < len(cells) else None

    @staticmethod
    def _warp_fired(start_map, start_pos, target, now_map, now_pos,
                    landing=None):
        """Did the warp at `target` actually fire?

        A different map is the obvious yes. But Victory Road, the Ice Path
        and Silver Cave stack their floors inside ONE map and join them
        with same-map warp_events, so the map name never changes and only
        the position teleports: stepping onto (13,31) lands on (13,17)
        fourteen rows away. Judging by map alone reported those ladders as
        failures the caller could not act on (FUCK_I_MESSED_UP #77).

        A same-map yes needs the LANDING CELL, not a distance. "moved more
        than 3 cells" was the first rule, and it reports success for a
        walk: re-entering Kurt's house exit (3,7), the tap fallback walks
        west to (0,7) -- 4 cells from where we stood, same map, no warp --
        and take_warp answered True with the player still indoors
        (tests/integration/test_take_warp_entry.py). `landing` comes from
        the map's own warp table (_same_map_landing); arrival drifts up to
        ~2 cells past the modeled cell (gotcha 14), so a same-map yes is
        "we JUMPED, and we came down on the paired cell". Both halves are
        needed: without the jump, walking two cells away from a landing we
        were already standing on reads as a teleport. No pairing, same
        map: not fired."""
        if now_map != start_map:
            return True
        if now_pos == target or landing is None:
            return False              # standing on it is not entering
        jumped = abs(now_pos[0] - start_pos[0]) + \
            abs(now_pos[1] - start_pos[1]) > 2
        return jumped and abs(now_pos[0] - landing[0]) + \
            abs(now_pos[1] - landing[1]) <= 2

    def _slide_is_clear(self, start, target):
        """Is every cell strictly between `start` and the aligned warp
        `target` free of OTHER warp tiles?

        A held slide stops on the first warp it touches, so a door whose
        pair sits between the two (Sprout Tower's (9,15)/(10,15) exit)
        can never be reached that way. Unknown map data answers True --
        the caller's fallback is a proper adjacent approach anyway."""
        (sx, sy), (tx, ty) = start, target
        dx = (tx > sx) - (tx < sx)
        dy = (ty > sy) - (ty < sy)
        x, y = sx + dx, sy + dy
        while (x, y) != (tx, ty):
            try:
                if self.tile_at(x, y) == "warp":
                    return False
            except Exception:
                return True
            x, y = x + dx, y + dy
        return True

    def take_warp(self, x, y, label=""):
        """ENTER the warp at (x, y) -- and standing on it is not entering.

        A warp fires when the player STEPS ONTO its tile with the key
        still down; arriving on one never re-triggers it. So a leg that
        ends standing on the tile (every door arrival) needs a step OFF
        and back ON, which is what cost turns at the Ilex/Azalea gate, the
        Union Cave north mouth, the Olivine pier and three ship cabins.
        `travel` reported that as `warp D at (3,41) -- expected
        ILEX_FOREST_AZALEA_GATE ... (step result: blocked)` when the real
        answer was "you are already on it".

        Order: step off if we are on it, walk adjacent if we are not, then
        enter held (doors need the key down) with `_step_warp_tap`'s
        phase-shifted taps as the fallback (staircases bounce held keys).
        True only when the MAP CHANGED; every False sets
        `last_warp_reason`."""
        self.last_warp_reason = None
        target = (int(x), int(y))
        start_map = self.map_name()
        if label:
            log.info(f"[take_warp {target}] {label} from {start_map} "
                     f"{self.pos()[2:]}")
        # Coordinates belong to a MAP. A caller holding coords from the
        # map it just left would otherwise be routed somewhere unrelated
        # and warped into whatever is there (observed live: stale gym
        # coords sent the walk into POKE_SEERS_HOUSE).
        try:
            const = self._map_const()
            known = self.nav.warps.get(const, {})
            checkable = True
        except Exception:          # duck-typed driver with no map data
            const, known, checkable = start_map, {}, False
        if checkable and self.tile_at(*target) != "warp" \
                and target not in known:
            return self._warp_fail(
                f"{target} is not a warp on {const} "
                f"(tile={self.tile_at(*target)}); warps here: "
                f"{[(e['x'], e['y'], e['to']) for e in self.exits() if e['kind'] == 'warp']}")
        if self.pos()[2:] == target:
            return self._reenter_warp(target, start_map)
        px, py = self.pos()[2:]
        # An aligned cell may enter with one held slide, but ONLY when
        # nothing between here and the door is itself a warp: at Sprout
        # Tower's exit the door's PAIRED tile sits in the way, so sliding
        # L from (11,15) toward (9,15) stopped dead on (10,15), fired
        # nothing (a south-wall door answers only to DOWN, gotcha 15) and
        # `travel` retried the same slide four times before giving up on a
        # perfectly open door.
        aligned = ((px == target[0]) != (py == target[1])) and \
            abs(px - target[0]) + abs(py - target[1]) <= 3 and \
            self._slide_is_clear((px, py), target)
        if not aligned:
            for mv, (dx, dy) in STEP.items():
                nx, ny = target[0] + dx, target[1] + dy
                try:                    # duck-typed driver: no map data
                    if self.tile_at(nx, ny) in ("blocked", "off-map"):
                        continue
                except Exception:
                    pass
                if self.goto(nx, ny, label or f"approach warp {target}"):
                    break
            else:
                return self._warp_fail(
                    f"no reachable cell adjacent to {target} "
                    f"(last goto: {self.last_goto_reason})")
        entry_pos = self.pos()[2:]
        r = self._held_warp_entry({"kind": "warp", "cell": target})
        if r == "battle":
            if not self._on_battle(f"take_warp {target}"):
                return self._warp_fail(
                    f"battle entering {target} and auto_fight=manual -- "
                    f"decide it, then retry")
        self.settle()
        landing = self._same_map_landing(target)
        if self._warp_fired(start_map, entry_pos, target, self.map_name(),
                            self.pos()[2:], landing):
            log.info(f"  -> {self.map_name()} {self.pos()[2:]}")
            return True
        if self.pos()[2:] != target:
            # This side did not fire it. Get ONTO the tile so the
            # exhaustive side walk can run -- it is the only code that
            # knows a door answers to one axis only.
            try:
                self.goto(*target)
            except Exception:
                pass
            if self.map_name() != start_map:      # walking on fired it
                self.settle()
                log.info(f"  -> {self.map_name()} {self.pos()[2:]}")
                return True
        if self.pos()[2:] == target:
            return self._reenter_warp(target, start_map)
        return self._warp_fail(
            f"entered {target} from {self.pos()[2:]} but the map is still "
            f"{start_map} (entry result: {r})")

    def _reenter_warp(self, target, start_map):
        """Step off the warp we are standing on and back onto it, trying
        every walkable side.

        The side matters: a door only fires when entered along its own
        axis (`CheckWarpFacingDown` and friends,
        engine/overworld/tile_events.asm), so re-entering a south-wall
        door sideways does nothing at all -- observed live on Cianwood
        Gym's exit, where stepping off RIGHT and back LEFT left the map
        unchanged."""
        inv = {"U": "D", "D": "U", "L": "R", "R": "L"}
        tried = []
        for mv, (dx, dy) in STEP.items():
            nx, ny = target[0] + dx, target[1] + dy
            if self.tile_at(nx, ny) in ("blocked", "off-map"):
                continue
            if self.pos()[2:] != target:      # a previous side left us off
                back = self._axis_move(target)
                if back and self._step(back) == "battle":
                    if not self._on_battle(f"take_warp {target}"):
                        return self._warp_fail(
                            f"battle re-entering {target} and "
                            f"auto_fight=manual -- decide it, then retry")
                if self.map_name() != start_map:
                    self.settle()
                    log.info(f"  -> {self.map_name()} {self.pos()[2:]}")
                    return True
                if self.pos()[2:] != target:
                    # `_axis_move` is a single step and cannot always get
                    # back (a south-wall door's only step-off is vertical,
                    # so the horizontal attempts leave us one cell away on
                    # the wrong axis). Falling through with `continue` here
                    # silently ATE the remaining sides, so U/D were never
                    # tried and the caller stranded off-target -- found by
                    # tests/integration/test_take_warp_entry.py on Kurt's
                    # house exit (3,7). Walk back properly instead: the
                    # docstring promises every walkable side, so try them.
                    try:
                        self.goto(*target)
                    except Exception:
                        pass
                    if self.pos()[2:] != target:
                        continue
            if self._step(mv) == "battle":
                if not self._on_battle(f"take_warp {target}"):
                    return self._warp_fail(
                        f"battle stepping off {target} and "
                        f"auto_fight=manual -- decide it, then retry")
            if self.map_name() != start_map:
                return True               # the step off was itself a warp
            if self.pos()[2:] == target:
                continue                  # could not step off this way
            tried.append(mv)
            off = self.pos()[2:]
            r = self.step_hold(inv[mv])
            if r == "battle":
                if not self._on_battle(f"take_warp {target}"):
                    return self._warp_fail(
                        f"battle entering {target} and auto_fight=manual "
                        f"-- decide it, then retry")
            if self.map_name() == start_map and self.pos()[2:] == target:
                r = self._step_warp_tap(inv[mv])
            self.settle()
            if self._warp_fired(start_map, off, target, self.map_name(),
                                self.pos()[2:],
                                self._same_map_landing(target)):
                log.info(f"  -> {self.map_name()} {self.pos()[2:]} "
                         f"(entered {inv[mv]})")
                return True
        if not tried:
            return self._warp_fail(
                f"standing on {target} with no walkable neighbour to step "
                f"off to")
        return self._warp_fail(
            f"stepped off {target} and back on from {'/'.join(tried)} and "
            f"the map is still {start_map} -- not an active warp?")

    def _axis_move(self, target):
        """The move that steps from here onto `target`, when adjacent."""
        px, py = self.pos()[2:]
        for mv, (dx, dy) in STEP.items():
            if (px + dx, py + dy) == tuple(target):
                return mv
        return None

    def step_dir(self, mv, max_frames=40):
        """Take exactly one step using the engine's own step state
        (wPlayerStepFlags: bit7 = step started, bit6 = step stopped).
        Returns 'moved' | 'blocked' | 'battle' | 'warp'."""
        before = self.pos()
        button = DIRS[mv].lower()
        for _attempt in range(2):  # a turn-in-place consumes the first "step"
            self.emu.py.button_press(button)
            started = False
            for _ in range(max_frames):
                self.emu.tick(1)
                if self.battle():
                    self.emu.py.button_release(button)
                    return "battle"
                if self.emu.read_u8("wPlayerStepFlags") & 0x80:
                    started = True
                    break
            self.emu.py.button_release(button)
            if not started:
                return "battle" if self.battle() else "blocked"
            for _ in range(48):  # ledge hops take longer than plain steps
                self.emu.tick(1)
                if self.emu.read_u8("wPlayerStepFlags") & 0x40:
                    break
            self.emu.tick(2)
            if self.battle():
                return "battle"
            now = self.pos()
            if now[:2] != before[:2]:
                return "warp"
            if now != before:
                return "moved"
        return "blocked"

    def _script_or_text(self):
        """Obstacle-prompt detector: wScriptMode != 0 OR a textbox. The
        whirlpool/waterfall/surf ask-menu raises wScriptMode==2 for ~60
        frames with textbox()==False and BLANK glyph text, so scene_busy's
        menu-cursor scrape never sees it."""
        try:
            if self.emu.read_u8("wScriptMode"):
                return True
        except Exception:
            pass
        return bool(self.textbox())

    def move_settled(self, mv, hold=40, max_frames=600, fight=None):
        """One directional move sampled SAFELY: press `mv` held `hold`
        frames, then poll pos() until it reads identical 3 times in a
        row -- a single read mid-slide/mid-walk reports the tile being
        crossed, so sampling right after the press lies. Textboxes are
        paged (A) en route.

        Battles are SURFACED, never swallowed: a step is not a journey,
        so an encounter returns 'battle' with the battle still up and
        the decision left to the caller (wren pt6 -- a model-written
        pacing loop reported fights=0 while this method fought ~20
        battles on the DEFAULT policy and whited the party out).
        `fight=True` restores the old play-it-out behaviour; `fight=None`
        follows self.auto_fight AND self.auto_fight_steps (False by
        default, i.e. surface it).
        Returns 'moved' | 'blocked' | 'warp' | 'battle'."""
        before = self.pos()
        self.press(f"{mv}:{hold}")
        last, stable = None, 0
        f0 = self.emu.frame
        while self.emu.frame - f0 < max_frames:
            if self.battle():
                if not self._step_fights(fight):
                    return "battle"
                self._on_battle(f"move_settled {mv}", fight=True)
                self.emu.tick(2)     # guarantee frame progress
                last, stable = None, 0
                continue
            if self.textbox():
                self.press("A:8 .:40")
                last, stable = None, 0
                continue
            cur = self.pos()
            if cur == last:
                stable += 1
                if stable >= 3:
                    break
            else:
                last, stable = cur, 1
            self.press(".:10")
        now = self.pos()
        if now[:2] != before[:2]:
            return "warp"
        return "moved" if now != before else "blocked"

    def _may_fight(self, fight=None):
        """Journey resolution of the tri-state `fight` argument: an
        explicit True/False wins, None follows self.auto_fight."""
        if fight is not None:
            return bool(fight)
        return bool(getattr(self, "auto_fight", True))

    def _step_fights(self, fight=None):
        """Step-primitive resolution of `fight`: an explicit True/False
        wins; None requires BOTH self.auto_fight and the opt-in
        self.auto_fight_steps, so the default is to surface the battle."""
        if fight is not None:
            return bool(fight)
        return (bool(getattr(self, "auto_fight", True))
                and bool(getattr(self, "auto_fight_steps", False)))

    def _on_battle(self, where="", fight=None):
        """The ONE path by which a nav/field helper plays out an encounter
        it walked into, so a policy/encounter hook always applies (every
        route goes through fight(), never a private shortcut).

        Returns True when the battle was fought -- the caller must still
        check _whiteout_stop() -- and False when it is handed BACK
        untouched (auto_fight=manual), with last_goto_reason set so the
        refusal is diagnosable instead of silent."""
        if not self._may_fight(fight):
            self.last_goto_reason = (
                f"battle during {where} (auto_fight=manual) -- "
                "decide: fight()/catch() yourself")
            log.info(f"  battle during {where}: handing it to the "
                     f"decider (auto_fight=manual)")
            return False
        self.fight()
        return True

    def clear_obstacle(self, direction, tries=6):
        """Clear a prompt-gated field obstacle one step in `direction`:
        whirlpools ($24), waterfalls ($33), and the surf-mount ask when
        stepping from land onto water. Live evidence (wren pt6): bumping
        one raises wScriptMode==2 for ~60 frames with textbox()==False
        and the YES/NO ask-menu drawn in BLANK glyphs -- real but
        invisible. A pause->A->pause cadence answers it; a fuzzer found
        sequences like '.:40 A:8 .:30' / 'U:40 .:40' work where tight
        mash loops always fail, so A presses keep >=40-frame gaps.
        Returns 'moved' (position or map changed), 'cleared-not-moved'
        (a prompt was answered but the follow-up step didn't take --
        retry a plain move), 'battle' (an encounter interrupted and
        auto_fight=manual: decide it, then retry), or 'failed' (no
        prompt ever appeared: plain wall)."""
        prompted = False
        for _attempt in range(tries):
            before = self.pos()
            self.press(f"{direction}:20 .:10")   # face + bump
            poked = False
            f0 = self.emu.frame
            while self.emu.frame - f0 < 90:
                if self.battle():
                    if not self._on_battle(f"clear_obstacle {direction}"):
                        return "battle"
                    self.emu.tick(2)
                    continue
                if self._script_or_text():
                    prompted = True
                    self.press("A:8 .:48")       # answer; >=40f gap
                    continue
                if not poked:
                    # facing-tile poke: the surf-mount ask ('The water
                    # is calm... SURF?') only appears on an explicit A
                    # while facing water -- bumping alone never asks.
                    self.press("A:8 .:40")
                    poked = True
                    continue
                self.press(".:10")
            for _ in range(8):                   # drain prompt chains
                if not self._script_or_text():
                    break
                prompted = True
                self.press("A:8 .:48")
            if self.pos() != before:             # the prompt itself moved
                return "moved"                   # us (surf mount)
            r = self.move_settled(direction, hold=40)
            if r == "battle":         # primitive surfaced it; one path
                if not self._on_battle(f"clear_obstacle {direction}"):
                    return "battle"
                continue
            if r in ("moved", "warp"):
                return "moved"
        return "cleared-not-moved" if prompted else "failed"

    def _pace_dirs(self, dirs, box):
        """The directions from the current cell that keep pace() inside
        `box` (x_lo, x_hi, y_lo, y_hi, inclusive). Already outside it (a
        warp dumped us elsewhere)? Only the moves that CLOSE the gap are
        offered, so the walk works its way back in instead of deadlocking."""
        if box is None:
            return list(dirs)
        x_lo, x_hi, y_lo, y_hi = box
        x, y = self.pos()[2:]

        def _miss(px, py):
            """Manhattan distance from (px, py) to the box; 0 = inside."""
            return (max(x_lo - px, 0, px - x_hi)
                    + max(y_lo - py, 0, py - y_hi))

        here = _miss(x, y)
        out = []
        for mv in dirs:
            dx, dy = STEP[mv]
            there = _miss(x + dx, y + dy)
            if there == 0 or there < here:
                out.append(mv)
        return out

    def pace(self, steps, dirs="UDLR", box=None, on_battle="return"):
        """Random-walk `steps` steps on the current map: the grinding /
        encounter-farming loop the driving model otherwise hand-rolls
        every session (and hand-rolled wrong -- see move_settled).

        `dirs`: directions to draw from ('LR' paces a corridor).
        `box`: (x_lo, x_hi, y_lo, y_hi) INCLUSIVE bounding box the walk
        may never leave -- an unclamped random walk drifted onto a
        staircase and stranded a live run three floors deep in Victory
        Road. Cells outside it are never stepped toward.
        `on_battle`: 'return' (default) STOPS the instant an encounter
        starts and leaves the battle up, so the model decides ko/catch/
        flee; 'fight' hands each one to the caller's policy (through
        fight(), so encounter_policy/default_policy apply) and keeps
        pacing.

        Returns {'steps': steps actually taken, 'battles': encounters
        seen, 'stopped': why it ended} where 'stopped' is 'steps'
        (budget spent), 'battle', 'boxed-in' (no legal direction),
        'blocked' (walls in every drawn direction), 'warp' (left the
        map), 'whiteout', or 'declined' (on_battle='fight' but
        auto_fight=manual -- the decider owns it)."""
        if on_battle not in ("return", "fight"):
            raise ValueError(f"pace: on_battle={on_battle!r} -- use "
                             f"'return' or 'fight'")
        budget = max(0, int(steps))
        picks = [c for c in str(dirs).upper() if c in STEP]
        if not picks:
            raise ValueError(f"pace: dirs={dirs!r} names no direction")
        if box is not None:
            box = tuple(int(v) for v in box)
            if len(box) != 4 or box[0] > box[1] or box[2] > box[3]:
                raise ValueError(
                    f"pace: box={box!r} must be (x_lo, x_hi, y_lo, y_hi) "
                    f"with lo <= hi")
        taken = battles = blocked = 0
        stopped = "steps"
        with self._money_watch(f"pace {budget} steps"):
            while taken < budget:
                legal = self._pace_dirs(picks, box)
                if not legal:
                    stopped = "boxed-in"
                    break
                r = self.move_settled(random.choice(legal), fight=False)
                if r == "battle":
                    battles += 1
                    if on_battle == "return":
                        stopped = "battle"
                        break
                    if not self._on_battle(f"pace step {taken + 1}"):
                        stopped = "declined"
                        break
                    if self._whiteout_stop("pace"):
                        stopped = "whiteout"
                        break
                    blocked = 0
                    continue
                if r == "warp":
                    taken += 1
                    stopped = "warp"   # off the map: the box means nothing
                    break
                if r == "moved":
                    taken += 1
                    blocked = 0
                    continue
                blocked += 1
                if blocked >= 8:
                    stopped = "blocked"
                    break
        out = {"steps": taken, "battles": battles, "stopped": stopped,
               "pos": self.pos()[2:]}
        if stopped == "blocked":
            # A caller that just re-calls pace() here spins forever on a
            # cell with no legal move (live: 7 real minutes of
            # "0/30 steps, stopped=blocked" in a Route 34 pocket). Name
            # the cell and the directions that were legal to TRY.
            out["blocked_dirs"] = list(self._pace_dirs(picks, box))
            log.info(f"  pace: boxed in at {out['pos']} -- every legal "
                     f"direction {out['blocked_dirs']} is a wall; move "
                     f"before pacing again")
        log.info(f"  pace: {taken}/{budget} steps, {battles} battles, "
                 f"stopped={stopped}")
        return out

    def walk(self, path, label=""):
        """Walk a path like 'L*12 U*3 D'. Handles battles, NPC dialogs, and
        map transitions along the way; reports blocks instead of looping.

        Every False return leaves the reason on `last_step_reason`."""
        if label:
            log.info(f"[{label}] from {self.map_name()} {self.pos()[2:]}")
        self.last_step_reason = None
        with self._money_watch(f"walk '{path}'"):
            for token in path.split():
                d, _, n = token.partition("*")
                d, n = d[0].upper(), int(n or 1)
                done = stuck = 0
                while done < n:
                    r = self._step(d)
                    if r == "battle":
                        if not self._on_battle(f"walk '{path}'"):
                            self.last_step_reason = (
                                f"walk '{path}': battle handed to the "
                                f"caller at {self.map_name()} "
                                f"{self.pos()[2:]}")
                            return False
                        if self._whiteout_stop(f"walk '{path}'"):
                            self.last_step_reason = (
                                f"walk '{path}': whiteout during the walk")
                            return False
                    elif r == "moved":
                        done += 1
                        stuck = 0
                    else:
                        if self.textbox():
                            self.flush_dialog()
                            continue
                        stuck += 1
                        if stuck == 2:
                            # close a stray menu, then retry
                            self.press("B:4 .:10")
                        if stuck >= 4:
                            self.last_step_reason = (
                                f"walk '{path}': blocked stepping {d} at "
                                f"{self.map_name()} {self.pos()[2:]}"
                                f" (last step: {r})")
                            log.warning(f"  BLOCKED {d} at "
                                        f"{self.map_name()} {self.pos()[2:]}")
                            return False
        return True

    def _resolve_map(self, name):
        """CONST_NAME or CamelCase (case/space-insensitive) -> CONST_NAME;
        None = current map."""
        if name is None:
            name = self.map_name()
        const = self._nav_resolve(name)
        if const is not None:
            return const
        raise SystemExit(f"unknown map {name!r}")

    def _goto_fail(self, reason, strict, where=""):
        """Loud goto failure (wren pt6: 'goto silently no-ops on
        unreachable targets'): record the machine-checkable reason on
        d.last_goto_reason, log the GAVE UP, and either return False
        or -- strict=True -- raise TravelError so callers that never
        check the return value stop instead of drifting."""
        self.last_goto_reason = reason
        log.warning(f"  GAVE UP ({reason})"
                    f"{' at ' + where if where else ''}")
        if strict:
            raise TravelError(f"goto: {reason}")
        return False

    def _goto_walk(self, x, y, label="", map_name=None, strict=False):
        """One walking attempt at (x,y): plan, walk, replan around NPC
        bumps, fight encounters on the way.

        This is `goto` minus the savestate escalation; every failure sets
        d.last_goto_reason ('outside-bounds: ...' / 'unreachable: ...' /
        'target-occupied: ...' / the give-up diagnoses). Callers other
        than `goto` should not use it -- goto is what decides whether a
        failure is worth escalating."""
        self._refresh_nav_blocks()
        goal_map = self._resolve_map(map_name)
        goal = (x, y)
        # An exit-warp cell of the CURRENT map as the goal (map not
        # requested) means "walk out through this door": hold onto the
        # tile, and success = having left the map. Escalating to cross-map
        # routing here just bounces in and out forever.
        exit_warp_goal = (map_name is None and goal_map == self.map_name()
                          and self._is_warp_cell(x, y))
        if goal_map == self.map_name():
            grid = self.nav.grid(goal_map)
            if not (0 <= x < len(grid[0]) and 0 <= y < len(grid)):
                return self._goto_fail(
                    f"outside-bounds: target ({x},{y}) outside {goal_map} "
                    f"bounds {len(grid[0])}x{len(grid)} -- pass map_name "
                    f"or use travel for cross-map goals", strict)
        entry_map = self.map_name()
        replans = idle = passes = drains = occupied = 0
        synced = False      # one live-grid re-sync per call, on a mystery
        edge_counts = {}    # (from_map, to_map): crossings this one call
        last_block = ""     # diagnosis text from the most recent blocked step
        reason = "unspecified"
        self.last_goto_reason = None
        if label or goal_map != self.map_name():
            log.info(f"[goto {goal}"
                  f"{'' if goal_map == self.map_name() else ' -> ' + goal_map}]"
                  f"{' ' + label if label else ''}".rstrip())
        while replans < 20 and idle < 40 and passes < 60:
            passes += 1
            cur_map, cur = self.map_name(), self.pos()[2:]
            if exit_warp_goal:
                if cur_map != entry_map:
                    log.info(f"  -> left through warp {goal}")
                    return True
            elif cur_map == goal_map and cur == goal:
                return True
            # a warp-tile goal fires the instant it is stepped on, so you
            # can never STAND on it when approaching from outside -- but
            # arrival never re-triggers, so coming out of goal_map's own
            # exit leaves you standing there. Accept proximity ONLY while
            # inside goal_map: requiring land[0] == cur_map instead once
            # blessed a PC-interior goal as "arrived" while we stood
            # outside on the street, having never walked in (silent
            # objective skip -- the straight-through killer).
            land = (self.nav.warps.get(goal_map, {}).get(goal)
                    and self.nav._warp_landing(goal_map, goal))
            if land and cur_map == goal_map and \
                    abs(cur[0] - land[1][0]) + abs(cur[1] - land[1][1]) <= 2:
                log.info(f"  -> arrived through warp {goal}")
                return True
            # NPCs scope to the replan's start map inside _bfs, so always
            # thread around them -- cross-map legs hit NPCs just the same.
            # STRENGTH boulders are the exception: they are pushable, so
            # with a knower in the party they are planned THROUGH and the
            # blocked-step handler below shoves them (Cianwood Gym).
            avoid = self.npc_cells()
            if self.can_push():
                avoid = avoid - self.boulder_cells()
            if goal_map == cur_map:
                # Same-map goal: stay on this map. Routing through warps
                # here just bounce-exits (e.g. standing north of Union
                # Cave's entrance carpet, every "shortcut" leaves the map).
                path = self.nav.find_path(cur_map, cur, goal, avoid)
                if not path:
                    # distinguish "NPC in the way" from "statically
                    # unreachable": a relaxed (ignore-NPC) route means
                    # some sprite squats a cell we must step through.
                    path = self.nav.find_path(cur_map, cur, goal)
                    if not path:
                        return self._goto_fail(
                            f"unreachable: no path from {cur} to {goal} "
                            f"on {cur_map}", strict, f"{cur_map} {cur}")
                    # which cells on the relaxed walk are squatted?
                    cx, cy = cur
                    squatted = []
                    for mv in path:
                        dx, dy = STEP[mv]
                        cx, cy = cx + dx, cy + dy
                        if (cx, cy) in avoid:
                            squatted.append((cx, cy))
                    waited_out = False
                    for cell in squatted:
                        if cell in self.boulder_cells() and self.can_push():
                            continue      # pushable: the walk shoves it
                        kind = self._blocker_kind(cell)
                        if kind == "stationary":
                            return self._goto_fail(
                                f"blocked-by-stationary-npc: {cell} on "
                                f"{cur_map} severs the only path to "
                                f"{goal} -- talk_to/face it, or route "
                                f"around", strict, f"{cur_map} {cur}")
                        if kind == "wanderer":
                            if self._wait_out_wanderer(cell):
                                waited_out = True
                                break
                            return self._goto_fail(
                                f"waited-for-wanderer: still blocked at "
                                f"{cell} on {cur_map} after "
                                f"{self.WANDER_WAIT_FRAMES}f", strict,
                                f"{cur_map} {cur}")
                    if waited_out:
                        continue        # replan against fresh sprites
                    if goal in avoid:
                        # unclassifiable blocker parked on the goal cell:
                        # walking there can only bump. Legacy diagnosis.
                        occupied += 1
                        if occupied >= 3:
                            return self._goto_fail(
                                f"target-occupied: NPC standing on {goal} "
                                f"({cur_map}) -- talk_to/face it from an "
                                f"adjacent cell instead", strict,
                                f"{cur_map} {cur}")
                    replans += 1
                    if replans % 5 == 1:
                        log.info(f"  threading {cur} -> {goal} past NPCs",
                              )
            else:
                path = self.nav.find_route(cur_map, cur, goal_map, goal,
                                           avoid)
                if not path:
                    relaxed = self.nav.find_route(cur_map, cur, goal_map,
                                                  goal)
                    if not relaxed:
                        return self._goto_fail(
                            f"unreachable: no route {cur_map} {cur} -> "
                            f"{goal_map} {goal}", strict,
                            f"{cur_map} {cur}")
                    replans += 1
                    if replans % 5 == 0:
                        log.info(f"  threading {cur} -> {goal} past NPCs",
                              )
                    path = relaxed
            moved = False
            for mv in path:
                r = self._step(mv)
                if r == "battle":
                    if not self._on_battle(f"goto {goal_map} {goal}"):
                        return False
                    if self._whiteout_stop(f"goto {goal_map} {goal}"):
                        self.last_goto_reason = "whiteout-abort"
                        return False
                    moved = True
                elif r == "warp":
                    here = self.map_name()
                    self.settle()
                    log.info(f"  -> {here} {self.pos()[2:]}")
                    key = (cur_map, here)
                    edge_counts[key] = edge_counts.get(key, 0) + 1
                    if edge_counts[key] > 2:
                        raise TravelError(
                            f"goto {goal_map} {goal}: map seam {key[0]} -> "
                            f"{key[1]} crossed {edge_counts[key]}x in one "
                            f"call -- ping-pong cycle, bailing; anchor at a "
                            f"known waypoint and relaunch")
                    moved = True
                    # step_hold keeps the key down through the transition,
                    # so the player glides past the modeled landing cell;
                    # replan from the live position rather than trust the
                    # rest of the precomputed path
                    break
                elif r == "moved":
                    moved = True
                elif r == "blocked":
                    # diagnose, don't just report: a blocked step on a
                    # grid-walkable cell is almost always a stray menu
                    # (gotcha 7), a textbox, or an NPC on the target cell
                    bx, by = self.pos()[2:]
                    dx, dy = STEP[mv]
                    if self.keyboard_open():
                        # An EGG HATCHES mid-walk and the naming keyboard
                        # eats every step, exactly like a stray menu --
                        # but it must never be blind-pressed (gotcha 18),
                        # so nav stops and hands the decision back. Live
                        # cost: TOGEPI hatched on ROUTE_34 and the run
                        # spent seven minutes and ~1200 "unexplained
                        # blocked step" replans walking into a keyboard.
                        return self._goto_fail(
                            "naming-keyboard: an egg hatched (or a catch "
                            "is naming) -- answer it with "
                            "dismiss_keyboard(name) and relaunch", strict)
                    if (bx + dx, by + dy) in self.boulder_cells() \
                            and self.can_push():
                        # A STRENGTH boulder is a shove, not a wall
                        # (Cianwood Gym's puzzle). clear_obstacle answers
                        # the "use STRENGTH?" prompt and takes the step.
                        r2 = self.clear_obstacle(mv)
                        log.info(f"  boulder at {(bx + dx, by + dy)}: "
                                 f"{r2}")
                        if r2 in ("moved", "cleared-not-moved"):
                            break          # replan from the new geometry
                    if self.textbox():
                        cause = " [textbox]"
                    elif self.menu_open():
                        cause = " [stray menu -- closing]"
                    elif (bx + dx, by + dy) in self.npc_cells():
                        cause = " [npc on target cell]"
                    else:
                        cause = ""
                    last_block = (cause.strip(" []")
                                  or "unexplained blocked step")
                    log.info(f"  blocked {mv} at {self.map_name()} "
                          f"{(bx, by)}{cause}")
                    if self.textbox():
                        # a scripted scene (Elm's call, the rival ambush)
                        # re-raises its textbox faster than flush_dialog's
                        # quiet window -- replanning against it storms to
                        # GAVE UP. Page the scene out (bounded, to
                        # wScriptMode==0) and replan without charging a
                        # storm strike.
                        dr = "timeout"
                        if drains < 3:
                            drains += 1
                            dr = self._drain_scene()
                        if dr == "menu":
                            # a choice opened mid-scene: mashing would
                            # pick something (gotcha 13) -- surface it
                            # WITH its labels so the decider can answer
                            # deliberately in one call
                            self.last_choice_options = \
                                self._choice_labels(self.emu.screen_text())
                            reason = (f"blocked by choice menu "
                                      f"{self.last_choice_options} -- "
                                      f"resolve_choice('YES') if answering "
                                      f"is safe (gotcha 13)")
                            self.last_goto_reason = reason
                            log.warning(f"  GAVE UP ({reason}) at "
                                  f"{self.map_name()} {self.pos()[2:]}")
                            return False
                        if dr in ("done", "battle"):
                            # drained (or a battle started: the next
                            # pass's _step returns 'battle' and the
                            # existing fight path takes it) -- retry via
                            # a fresh plan, uncounted
                            break
                        self.flush_dialog()
                    elif self.menu_open():
                        self.close_menus()
                    else:
                        # Unexplained: no textbox, no menu, no NPC. Either
                        # a wanderer is mid-step, or nav's grid is STALE --
                        # a cut tree REGROWS on map re-entry and nav still
                        # had the gap, so twenty replans walked into a tree
                        # that was standing right there. Re-sync from the
                        # engine's own block map once per goto, then let a
                        # wanderer move.
                        if not synced:
                            synced = True
                            try:
                                stale = self.sync_grid()
                            except Exception:
                                stale = None   # reduced driver: no live map
                            if stale:
                                log.info("  grid was stale here; replanning "
                                         "against the live map")
                                replans += 1
                                break
                        self.press(".:40")  # let a wandering NPC step aside
                    replans += 1
                    break
            else:
                continue   # path exhausted; loop re-checks arrival/replans
            if not moved:
                idle += 1
        if idle >= 40:
            reason = f"no-progress ({idle} idle passes)"
            try:
                if self.emu.read_u8("wScriptMode"):
                    reason += "; script-scene-active"
            except Exception:
                pass
        elif replans >= 20:
            reason = f"replan-storm ({replans} replans)"
        elif passes >= 60:
            reason = "pass-cap"
        if last_block:
            reason += f"; last-block={last_block}"
        if "script-scene-active" in reason:
            reason += ("; if crossing the scene cell is talk-only-safe, "
                       "set d.trip_scenes=True for this one goto")
        return self._goto_fail(
            reason, strict,
            f"{self.map_name()} {self.pos()[2:]} -> {goal_map} {goal}")

    GOTO_ESCALATE_MOVES = 60

    GOTO_ESCALATE_NODES = 40

    GOTO_ESCALATE_ON = ("no-path", "unreachable", "replan-storm",
                        "no-progress", "pass-cap", "outside-bounds")

    GOTO_NO_ESCALATE_ON = ("npc", "target-occupied", "script-scene-active",
                           "choice menu", "whiteout", "manual",
                           "waited-for-wanderer")

    GOTO_HANDOFF = ("manual", "choice menu", "whiteout")

    _escalating = False

    def _should_escalate(self, reason):
        """Is this goto failure the kind a savestate search can fix?"""
        if not reason:
            return False
        low = reason.lower()
        if any(k in low for k in self.GOTO_NO_ESCALATE_ON):
            return False
        return any(k in low for k in self.GOTO_ESCALATE_ON)

    def goto(self, x, y, label="", map_name=None, strict=False,
             escalate=True):
        """BFS-pathfind to (x,y) and walk it. Defaults to the current map;
        pass map_name (CONST_NAME or CamelCase) to route across maps via
        warp events and edge connections. Replans around NPC bumps; fights
        encounters on the way.

        When the walk fails because the decoded MAP is wrong -- no path,
        replan storm, no progress -- goto escalates by itself to the
        savestate search (explore_bfs), which walks the real geometry
        instead of the parsed grid. That is what `reach` used to be for,
        and nothing called it: the Indigo Plateau Pokecenter renders (3,8)
        as wall while the avatar stands on it, so 20 replans burned and
        the leg was hand-driven with step_hold. Failures that a search
        cannot fix (an NPC in the way, a live scene, a choice menu, a
        whiteout) are reported immediately instead.

        `escalate`: True for the default budget, False to refuse, or a
        (max_moves, max_nodes) tuple to raise it (what reach does).

        Failure is loud, never silent: every False return sets
        d.last_goto_reason first. strict=True upgrades navigation
        failures to TravelError; interactive handoffs (manual battle,
        choice menu, whiteout recovery) still return False under strict
        so the decider can take over."""
        with self._money_watch(f"goto {(x, y)}"):
            if self._goto_walk(x, y, label, map_name, strict=False):
                return True
            reason = self.last_goto_reason
            if escalate and not self._escalating and \
                    self._should_escalate(reason):
                if isinstance(escalate, (tuple, list)):
                    moves, nodes = escalate
                else:
                    moves, nodes = (self.GOTO_ESCALATE_MOVES,
                                    self.GOTO_ESCALATE_NODES)
                goal, goal_map = (x, y), self._resolve_map(map_name)
                log.warning(f"  goto ({reason}) -- escalating to a "
                            f"savestate search ({moves} moves, {nodes} "
                            f"nodes): the decoded grid may be wrong")
                self._escalating = True
                try:
                    found = self.explore_bfs(
                        lambda dr: dr.pos()[2:] == goal
                        and dr.map_name() == goal_map,
                        max_moves=moves, max_nodes=nodes,
                        on_battle="fight")
                except Exception as exc:
                    # The search needs savestates; where they are not
                    # available the WALK's failure is still the answer.
                    log.warning(f"  savestate search unavailable "
                                f"({type(exc).__name__}: {exc})")
                    found = None
                finally:
                    self._escalating = False
                if (found or {}).get("found"):
                    self.last_goto_reason = None
                    log.info(f"  -> reached {goal} via savestate search")
                    return True
                reason = f"{reason}; search exhausted ({nodes} nodes)"
                self.last_goto_reason = reason
            if strict and reason and not any(
                    k in reason.lower() for k in self.GOTO_HANDOFF):
                raise TravelError(f"goto: {reason}")
            return False

    trip_scenes = False

    def _scene_spent(self, camel, script):
        """True when the coord_event's OWN leading guard chain proves it
        does nothing right now.

        This is what makes a scene block expire. `nav.blocked` is
        recomputed from the map source on every goto, so a cell whose
        scene token still matches came back forever -- at
        INDIGO_PLATEAU_POKECENTER_1F the map declares exactly one scene
        (id 0 = RIVAL_BATTLE) and its post-battle script sets the scene
        to that same id, so (16,4)/(17,4) -- the only corridor to the
        League door -- were re-severed after every single failed goto and
        had to be cleared by hand three times (session claude pt12).

        The script says so itself:
            checkevent EVENT_BEAT_RIVAL_IN_MT_MOON / iffalse ...Done
            checkflag ENGINE_INDIGO_PLATEAU_RIVAL_FIGHT / iftrue ...Done
        Evaluate those live: if a guard jumps to a label that is not
        disruptive, walking the cell cannot push us anywhere. Flags that
        cannot be resolved are left alone (assume the worst)."""
        for check, name, jump, target in script_guards(self.nav._repo,
                                                       camel, script):
            try:
                val = (self._event_flag(name) if check == "checkevent"
                       else self.engine_flag(name))
            except Exception:
                continue                   # unknown flag: assume armed
            if val is not (jump == "iftrue"):
                continue                   # this guard falls through
            if not script_is_disruptive(self.nav._repo, camel, target):
                return True
        return False

    def _refresh_nav_blocks(self):
        """Mark every coord_event cell that would fire RIGHT NOW unwalkable
        for planning: its scene token matches the map's live scene id (or
        is SCENE_ALWAYS/-1), or the scene state can't be read (assume the
        worst). Scenes only ever move forward via setscene, so this keeps
        e.g. Route 32's eternally re-firing Cooltrainer cutscene out of
        BFS. Cheap: parses are process-cached, one WRAM byte per map."""
        events = coord_events(self.nav._repo)
        consts = scene_consts(self.nav._repo)
        syms = scene_vars(self.nav._repo)
        blocks = {}
        for const, evs in events.items():
            sym = syms.get(const)
            cur = None                     # None = no persistent scene id
            if sym:
                try:
                    cur = self.emu.read(self.emu.sym[sym], 1)[0]
                except Exception:
                    cur = "unreadable"
            order = {n: i for i, n in enumerate(consts.get(const, [])) if n}
            cells = set()
            camel = self.nav.camel.get(const, const)
            for x, y, tok, script in evs:
                v = -1 if tok.startswith("-") else order.get(tok)
                if v == -1:
                    fires = True           # SCENE_ALWAYS: fires every time
                elif v is None:
                    fires = True           # unknown scene: assume the worst
                elif cur is None:
                    fires = False          # no scene var -> engine sees -1
                elif cur == "unreadable":
                    fires = True           # can't tell -> assume the worst
                else:
                    fires = cur == v
                if not fires:
                    continue
                if not script_is_disruptive(self.nav._repo, camel, script):
                    continue
                if self._scene_spent(camel, script):
                    continue               # guard chain already answered
                if script_advances_scene(self.nav._repo, camel, script, tok):
                    continue     # one-shot: it fires once, then it is gone
                if self.trip_scenes:
                    log.info(f"  [trip_scenes] crossing {const} scene cell "
                          f"{(x, y)} unblocked")
                    continue
                cells.add((x, y))
            if cells:
                blocks[const] = cells
        self.nav.blocked = blocks

    def blocked_cells(self, map_name=None):
        """The coord_event cells nav currently refuses to plan through,
        ``{map: {(x, y), ...}}`` (or one map's set). Refreshed from live
        scene/flag state first, so this is what BFS will actually see --
        the answer to "why is there no path" when the grid looks open."""
        self._refresh_nav_blocks()
        if map_name is None:
            return {m: set(c) for m, c in self.nav.blocked.items()}
        return set(self.nav.blocked.get(self._resolve_map(map_name), ()))

    def _mg_edges(self):
        """{from_map_const: [routable edges]} over data/mapgraph.json."""
        adj = {}
        for e in mapgraph()["edges"]:
            if e.get("routable"):
                adj.setdefault(e["from_map"], []).append(e)
        return adj

    def _edge_steps(self, e):
        """All ways to walk edge `e`: [((ax, ay), dir_letter), ...] sorted
        closest-first, where standing on (ax, ay) and stepping `dir` fires
        the warp/connection. Validated against this repo's collision grids;
        an edge the terrain doesn't allow yields []. None for maps with no
        grid. Multiple candidates matter: the same door can have a walkable
        approach from one side only (Union Cave's door is entered stepping
        UP off the ledge lip below it -- its north cell is walled off)."""
        try:
            grid = self.nav.grid(e["from_map"])
        except KeyError:
            return None
        hgt, wid = len(grid), len(grid[0])

        surf = bool(getattr(self.nav, "surf", False))

        def standable(x, y):
            # WATER counts once self.enable_surf() flips nav.surf:
            # Route 40 -> 41 is crossed on a Surf mount and its whole
            # border band is sea, so a land-only band made the planner
            # answer "no routable mapgraph path" with SURF in the party
            return (0 <= x < wid and 0 <= y < hgt
                    and (grid[y][x] in WALKABLE or grid[y][x] in HOPS
                         or (surf and grid[y][x] in _NAV_WATER))
                    and grid[y][x] not in WARPS)

        if e["kind"] == "warp":
            tx, ty = e["cells"]
            cands = [((tx - dx, ty - dy), d)
                     for d, (dx, dy) in STEP.items()
                     if standable(tx - dx, ty - dy)]
        elif e["kind"] == "connection":
            d = _CONN_LETTER[e["entry"]["heading"]]
            dx, dy = STEP[d]
            (x1, y1), (x2, y2) = e["cells"]
            cands = []
            for x in range(min(x1, x2), max(x1, x2) + 1):
                for y in range(min(y1, y2), max(y1, y2) + 1):
                    # stand ON the border band; the step in `d` leaves the
                    # map and fires the connection
                    nx, ny = x + dx, y + dy
                    if standable(x, y) and not (0 <= nx < wid
                                                and 0 <= ny < hgt):
                        cands.append(((x, y), d))
        else:
            return None
        if not cands:
            return []
        px, py = self.pos()[2:]
        cands.sort(key=lambda c: (abs(c[0][0] - px) + abs(c[0][1] - py),
                                  c[0]))
        return cands

    def _regions(self, m, x, y):
        """nav.regions_at with the planner's wildcard convention: (-1,)
        for maps with no grid (regions unknowable there) or cells sealed
        all around -- region -1 matches anything."""
        try:
            r = self.nav.regions_at(m, x, y)
        except KeyError:
            return (-1,)
        return r or (-1,)

    TRANSITION_COST = 60

    DEFAULT_MAX_COST = 700   # rejects Johto-ring plans (~1500+) while

    def route(self, dest_map, max_cost=None):
        """Plan-only cross-map route to `dest_map`: Dijkstra over
        mapgraph.json's validated warp/connection edges with walk-distance
        costs, expanded into per-leg steps --
        [{"kind": "walk", "map", "x", "y"}, {"kind": "warp"|"connection",
        "from", "to", "dir", ...}, ...]. Nodes are (map, region) over
        nav.region_map components, gated by each edge's from_regions /
        to_regions (absent field = wildcard, for grid-less maps): a warp
        on a walled-off part of a map -- Sprout Tower 2F's (10,14) stairs
        seen from the east arrival area -- is never planned; the real
        route detours over the 1F walkway. The entry region comes from
        the CURRENT standing cell (self.pos()). Raises LookupError when
        nothing routes, or when the cheapest plan exceeds max_cost (a
        "detour ring" -- almost certainly not what a straight-through run
        wants; anchor closer or raise max_cost deliberately). Never moves
        the player."""
        self._refresh_nav_blocks()
        dest = self._resolve_map(dest_map)
        src = self.map_name()
        if max_cost is None:
            max_cost = self.DEFAULT_MAX_COST
        if dest == src:
            return []
        adj = self._mg_edges()
        px, py = self.pos()[2:]
        best, entry, prev, heap = {}, {}, {}, []
        for r in self._regions(src, px, py):
            node = (src, r)             # region -1 = wildcard
            best[node] = 0
            entry[node] = (px, py)    # where we'd start walking there
            prev[node] = None
            heap.append((0, node))
        heapq.heapify(heap)
        seen = set()
        goal = None
        while heap:
            cost, node = heapq.heappop(heap)
            if node in seen:
                continue
            seen.add(node)
            m, reg = node
            if m == dest:
                goal = node       # any region of the destination map
                break
            ex, ey = entry[node]
            # scene seals are plan-truth too: an edge whose only
            # approach cells sit behind an armed coord_event is not
            # routable RIGHT NOW, even though the collision grid says
            # otherwise (Route 32 descent, Azalea neck approaches)
            sealed = self.nav.blocked.get(m, ())
            for e in sorted(adj.get(m, ()),
                            key=lambda e: (e["to_map"], e["kind"],
                                           json.dumps(e["cells"]))):
                frm = e.get("from_regions")
                if frm is not None and reg >= 0 and reg not in frm:
                    continue    # warp sits on a walled-off part of m
                cands = [c for c in self._edge_steps(e)
                         if c[0] not in sealed
                         and (reg < 0
                              or reg in self._regions(m, *c[0]))]
                if not cands:
                    continue
                walk = min(abs(ax - ex) + abs(ay - ey)
                           for (ax, ay), _ in cands)
                ncost = cost + walk + self.TRANSITION_COST
                (ax, ay), d = min(cands, key=lambda c: (
                    abs(c[0][0] - ex) + abs(c[0][1] - ey), c[0]))
                land = (tuple(e["dest_cell"]) if e["kind"] == "warp"
                        else None)
                if land is None:
                    # connection landing depends on the departure cell;
                    # approximate with the far edge cell toward travel
                    grid = self.nav.grid(e["to_map"])
                    land = (len(grid[0]) - 1 if d == "L" else 0, ey)
                for nr in (e.get("to_regions") or (-1,)):
                    nxt = (e["to_map"], nr)
                    if nxt in seen:
                        continue
                    if nxt in best and best[nxt] <= ncost:
                        continue
                    best[nxt] = ncost
                    prev[nxt] = (node, e, (ax, ay), d, cands)
                    entry[nxt] = land
                    heapq.heappush(heap, (ncost, nxt))
        if goal is None:
            raise LookupError(f"no routable mapgraph path {src} -> {dest}")
        if best[goal] > max_cost:
            raise LookupError(
                f"cheapest {src} -> {dest} plan costs {best[goal]} "
                f"(> max {max_cost}) -- detour ring; anchor at a nearer "
                f"waypoint or pass a deliberate max_cost")
        hops, node = [], goal
        while prev[node]:
            pnode, e, a, d, cands = prev[node]
            hops.append((pnode[0], e, a, d, cands))
            node = pnode
        steps = []
        for frm, e, (ax, ay), d, cands in reversed(hops):
            steps.append({"kind": "walk", "map": frm, "x": ax, "y": ay,
                          "why": f"approach {e['kind']} to {e['to_map']}"})
            trans = {"kind": e["kind"], "from": frm, "to": e["to_map"],
                     "dir": d, "notes": e.get("notes"),
                     "approaches": [{"x": a[0][0], "y": a[0][1], "dir": a[1]}
                                    for a in cands]}
            if e["kind"] == "warp":
                trans["cell"] = list(e["cells"])
                trans["warp_id"] = e.get("warp_id")
                trans["dest"] = list(e["dest_cell"])
            else:
                trans["band"] = [list(c) for c in e["cells"]]
                trans["offset"] = e.get("offset")
            steps.append(trans)
        return validate_route(steps)

    def _landing(self, st, x, y):
        """Modeled landing cell for transition step `st` taken at (x, y)."""
        if st["kind"] == "warp":
            return tuple(st["dest"])
        grid = self.nav.grid(st["to"])
        return _CONN_LAND[st["dir"]](len(grid[0]), len(grid),
                                     st["offset"] or 0, x, y)

    def reach(self, x, y, label="", budget=200, nodes=140):
        """Walk to (x, y) on THIS map with a BIGGER savestate-search
        budget than goto's default.

        Victory Road (and the Rocket base, and Ice Path) have floors whose
        decoded grid disagrees with the live map: the walk reports
        'unexplained blocked step' / 'unreachable' for cells the avatar
        can plainly walk to. That escalation now lives in `goto` itself,
        so this is just goto with the budget raised -- the walk is still
        tried first, and only its failure pays for a search.
        Returns True when standing on (x, y)."""
        if self.pos()[2:] == (x, y):
            return True
        try:
            return self.goto(x, y, label, escalate=(budget, nodes))
        except TravelError:
            return self.pos()[2:] == (x, y)

    EDGE_SLIDE = 6

    def _slide_edge(self, st, dest=""):
        """Cross a map-edge connection whose planned row/column does not
        fire, by sliding ALONG the edge and re-trying the held step.

        `travel` used to fail the whole leg here: Azalea Town's east edge
        crosses at y=14 while the plan said y=13, and Route 32 -> Violet
        at x=8. A hand-written `cross()` helper doing exactly this slide
        was what got a live session through both, so it belongs inside
        travel. Returns True when the map changed."""
        d = st["dir"]
        start_map = self.map_name()
        # slide perpendicular to the crossing direction: out one way,
        # back to the start, then out the other. Alternating U/D on the
        # spot just oscillates around the row that does not work.
        pairs = (("U", "D"), ("D", "U")) if d in ("L", "R") \
            else (("L", "R"), ("R", "L"))
        for mv, back in pairs:
            moved = 0
            for _ in range(self.EDGE_SLIDE):
                if self._step(mv) != "moved":
                    break
                moved += 1
                r = self.step_hold(d)
                if r == "battle":
                    if not self._on_battle(f"travel -> {dest}"):
                        return False
                if self.map_name() != start_map:
                    log.info(f"  edge slide: crossed {d} after {moved} "
                             f"{mv}-step(s) -- the planned row did not fire")
                    return True
            for _ in range(moved):        # back to the planned row
                if self._step(back) != "moved":
                    break

        return False

    def step_off_warp(self):
        """If we are standing ON a live warp, take one step off it.

        Every door arrival lands on one (gotcha 15), and a caller's next
        step can re-enter it -- Ecruteak's gym door bounced city<->gym
        three times before the ping-pong guard bailed the leg. Returns the
        direction stepped, or None when there was nothing to do (not on a
        warp, or no walkable non-warp neighbour)."""
        try:
            x, y = self.pos()[2:]
            if self.tile_at(x, y) != "warp":
                return None
        except Exception:
            return None
        here_map = self.map_name()
        for mv, (dx, dy) in STEP.items():
            if self.tile_at(x + dx, y + dy) in ("blocked", "off-map", "warp"):
                continue
            if (x + dx, y + dy) in self.npc_cells():
                continue
            if self._step(mv) == "moved" and self.map_name() == here_map:
                log.info(f"  stepped {mv} off the arrival warp {(x, y)}")
                return mv
        return None

    def travel(self, dest_map, label=""):
        """Execute route(<dest_map>) leg by leg with the existing walk/
        _step/settle mechanics: goto each approach cell, hold through warps
        (_step picks step_hold on warp tiles -- the Route 31 gate only
        fires with the key held sideways), settle() after every transition,
        then verify landing map + cell. Small drift past the modeled
        landing is expected (held key glides ~2 cells; AGENTS.md gotcha
        14); anything worse raises TravelError. If an edge's approach cell
        is unreachable from our side (one-way ledges/walls), falls back to
        that edge's other approaches. A tolerated glide that lands across
        a region seam (door tile touching two rooms) replans the remainder
        from the live cell -- route() rereads pos() for its entry region."""
        dest = self._resolve_map(dest_map)
        self._refresh_nav_blocks()
        if self.map_name() == dest:
            return []
        steps = self.route(dest)
        log.info(f"[travel -> {dest}] {len(steps)} steps from "
              f"{self.map_name()} {self.pos()[2:]}"
              f"{' ' + label if label else ''}".rstrip())
        _edge_counts = {}
        i = 0
        while i < len(steps):
            st = steps[i]
            cur = self.map_name()
            if st["kind"] == "walk":
                if cur != st["map"]:
                    raise TravelError(f"leg {i}: plan expects "
                                      f"{st['map']}, we're on {cur}")
                nxt = steps[i + 1] if i + 1 < len(steps) else None
                alts = (nxt.get("approaches") if nxt else None) or []
                if self.goto(st["x"], st["y"], f"travel -> {dest}"):
                    for alt in alts:
                        if [alt["x"], alt["y"]] == [st["x"], st["y"]]:
                            nxt["dir"] = alt["dir"]
                    i += 1
                    continue
                # this approach may sit on the far side of a one-way ledge
                # or wall -- fall back to the edge's other approaches
                for alt in alts:
                    if [alt["x"], alt["y"]] == [st["x"], st["y"]]:
                        continue
                    log.info(f"  approach {(st['x'], st['y'])} unreachable; "
                          f"trying {alt['dir']} from "
                          f"{(alt['x'], alt['y'])}")
                    if self.goto(alt["x"], alt["y"], f"travel -> {dest}"):
                        nxt["dir"] = alt["dir"]
                        break
                else:
                    raise TravelError(
                        f"leg {i}: no path to any approach of the next "
                        f"{nxt['kind'] if nxt else 'transition'} on {cur} "
                        f"(last goto: {self.last_goto_reason})")
                i += 1
                continue
            key = json.dumps([st["kind"], st["from"], st["to"], st["dir"],
                              st.get("cell") or st.get("band")], sort_keys=True)
            edge_count = _edge_counts.get(key, 0) + 1
            _edge_counts[key] = edge_count
            if edge_count > 2:
                raise TravelError(
                    f"leg {i}: transition {st['from']} -> {st['to']} via "
                    f"{st['dir']} executed {edge_count}x this travel() -- "
                    f"bailing out instead of ping-ponging")
            if cur != st["from"]:
                raise TravelError(f"leg {i}: plan transitions from "
                                  f"{st['from']}, we're on {cur}")
            px, py = self.pos()[2:]
            expected = self._landing(st, px, py)
            r = None
            # Already standing ON the warp tile: a warp only fires on the
            # step that enters it, so stepping `dir` from here just walks
            # away. take_warp steps off and back on.
            if st["kind"] == "warp" and \
                    tuple(st.get("cell") or ()) == (px, py):
                if self.take_warp(px, py, f"travel -> {dest}"):
                    r = "warp"
                elif self.map_name() == st["from"]:
                    raise TravelError(
                        f"leg {i}: standing on warp {(px, py)} and could "
                        f"not enter it ({self.last_warp_reason})")
            if r is None:
                for _attempt in range(4):
                    r = self._step(st["dir"])
                    if r == "battle":
                        # encounter mid-transition; then retry
                        if not self._on_battle("travel"):
                            raise TravelError(
                                f"leg {i}: battle mid-travel with "
                                f"auto_fight=manual -- decide it "
                                f"(fight()/catch()), then relaunch travel()")
                        if self._whiteout_stop("travel"):
                            raise TravelError(
                                f"leg {i}: wiped mid-travel, auto-healed at "
                                f"{self.map_name()} -- relaunch travel()")
                    elif r == "blocked":
                        if self.textbox():
                            # scripted scene on the transition cell: page
                            # it out (bounded); a battle it starts is
                            # caught by the next attempt's _step -> the
                            # fight path above
                            self._drain_scene()
                        else:
                            break
                    elif r != "warp" and self.map_name() == st["from"]:
                        # stepped but the warp didn't fire. On a multi-warp
                        # door row (Sprout Tower 1F's double door) the held
                        # step GLIDES across every door tile without firing
                        # (gotcha 12); each retry then re-crosses the row
                        # from the other side -- the observed (8,15)<->
                        # (11,15) ping-pong. take_warp drives back onto the
                        # tile properly, including from ON it.
                        if st["kind"] == "warp" and st.get("cell"):
                            if self.take_warp(*st["cell"],
                                              f"travel -> {dest}"):
                                r = "warp"
                                break
                        continue
                    else:
                        break
            self.settle()
            if self.map_name() == st["from"] and st["kind"] == "connection":
                # Map-edge connections are a BAND, and the planned row can
                # be off by one (Azalea's east edge fires at y=14, the plan
                # said 13; Route 32 -> Violet at x=8). Slide along the edge
                # and retry with a held step rather than failing the leg.
                if self._slide_edge(st, dest):
                    r = "warp"
                self.settle()
            mx, my = self.pos()[2:]
            here = self.map_name()
            if here != st["to"]:
                raise TravelError(
                    f"leg {i}: {st['kind']} {st['dir']} at {(px, py)} -- "
                    f"expected {st['to']}, on {here} {(mx, my)} "
                    f"(step result: {r})")
            drift = abs(mx - expected[0]) + abs(my - expected[1])
            log.info(f"  -> {here} {(mx, my)} (drift {drift})")
            if here == dest:
                # Landed. Every door arrival leaves the avatar standing ON
                # a live warp (gotcha 15), and the NEXT goto's first step
                # can re-enter it: Ecruteak's gym door ping-ponged
                # city<->gym three times and bailed. Step off before
                # handing control back.
                self.step_off_warp()
                return steps      # landed on the destination: done
            # The map CHANGED to the expected one, so the crossing fired;
            # where it put us is arrival drift (gotcha 14), not a failure.
            # Failing the leg on drift > 3 aborted a perfectly good
            # Azalea -> ROUTE_33 crossing that landed 5 cells along the
            # border band. Replan the remainder from the live cell -- the
            # same thing the region-seam case below does.
            if drift > 3:
                log.info(f"  landing drifted {drift} from the model "
                         f"{expected}; replanning the remainder")
                steps = steps[:i + 1] + self.route(dest)
            # the glide can carry the landing across a region seam: the
            # rest of the plan then walks the wrong side of a wall.
            elif drift and set(self._regions(here, mx, my)).isdisjoint(
                    self._regions(here, *expected)):
                log.info("  landing crossed a region seam; replanning "
                         "remainder from live cell")
                steps = steps[:i + 1] + self.route(dest)
            i += 1
        return steps

    def _explore_snap(self):
        """Current emulation as an in-memory savestate blob."""
        buf = BytesIO()
        self.emu.py.save_state(buf)
        return buf.getvalue()

    def _explore_restore(self, blob):
        self.emu.py.load_state(BytesIO(blob))
        self.emu.tick(5)            # let the restored frame re-latch

    def _explore_settled_move(self, mv, on_battle, max_frames=1200):
        """One directional move driven to a SETTLED end state for
        explore_bfs: ice slides and warp glides keep the avatar moving
        with no input, forced signs pop textboxes mid-move, and wilds/
        trainers intercept. Polls pos() until stable, answers textboxes
        with A at 40+ frame gaps, and resolves battles per `on_battle`
        ('fight' | 'skip'). A move that gets nowhere retries once with
        _step_warp_tap: COLL_STAIRCASE tiles push a held key straight
        back off, so Victory Road's inter-floor stairs read as walls to a
        held-key search (the avatar even STANDS on them without firing).
        Returns 'moved' | 'blocked' | 'skip' (skip = dead branch)."""
        before = self.pos()

        def _drive():
            last, quiet = None, 0
            f0 = self.emu.frame
            while self.emu.frame - f0 < max_frames:
                if self.battle():
                    if on_battle != "fight":
                        return "skip"
                    self.fight()
                    if getattr(self, "_whiteout_pending", False):
                        self._whiteout_pending = False   # the BRANCH died
                        return "skip"
                    last, quiet = None, 0
                    continue
                if self.textbox():
                    self.press("A:4 .:40")   # 40+ frame gap between answers
                    last, quiet = None, 0
                    continue
                cur = self.pos()
                if cur == last:
                    quiet += 1
                    if quiet >= 3:
                        break
                else:
                    last, quiet = cur, 0
                self.emu.tick(20)
            return None

        self.step_dir(mv)
        out = _drive()
        if out:
            return out
        if self.pos() != before:
            return "moved"
        try:
            self._step_warp_tap(mv)     # staircase phase-shifted taps
        except Exception:
            return "blocked"
        out = _drive()
        if out:
            return out
        return "moved" if self.pos() != before else "blocked"

    def explore_bfs(self, goal, max_moves=600, dirs="URDL", forbid_maps=(),
                    on_battle="fight", max_nodes=400):
        """Savestate breadth-first exploration (wren pt6: hand-rolled 10+
        times this run for ice slides, the Rocket base, Tohjo Falls).
        BFS over settled directional moves from the CURRENT state, with
        in-memory savestates as nodes and the frontier keyed by
        (map, x, y). `goal` is a callable(driver) -> bool evaluated
        after EVERY settled move -- a mid-move map change is an
        evaluation point too. States on `forbid_maps` (map names) are
        goal-checked but never expanded. on_battle='fight' plays
        intercepts out with fight(); 'skip' abandons that branch.
        Budgets: `max_moves` settled moves, `max_nodes` distinct
        (map, x, y) states -- snapshots live in memory only, keep the
        cap modest.

        Returns {'found': bool, 'state': bytes|None, 'steps': int,
        'visited': int, 'cells': set[(map, x, y)]}. On found, the winning
        savestate IS the loaded emulation state (the returned blob is a
        keepsake); on not-found the starting state is reloaded. `cells` is
        the frontier actually proven reachable -- read it to pick the next
        waypoint when a floor's static grid lies about its geometry."""
        forbid = set(forbid_maps)
        self.settle()
        if goal(self):
            return {"found": True, "state": self._explore_snap(),
                    "steps": 0, "visited": 1,
                    "cells": {(self.map_name(),) + self.pos()[2:]}}
        root = self._explore_snap()
        seen = {(self.map_name(),) + self.pos()[2:]}
        q = deque([(root, 0)])
        moves = 0
        while q and moves < max_moves and len(seen) < max_nodes:
            blob, depth = q.popleft()
            for mv in dirs:
                if moves >= max_moves or len(seen) >= max_nodes:
                    break
                self._explore_restore(blob)
                moves += 1
                out = self._explore_settled_move(mv, on_battle)
                if out == "skip":
                    continue              # dead branch; state is junk
                if goal(self):
                    state = self._explore_snap()
                    log.info(f"  explore_bfs: goal at {self.map_name()} "
                             f"{self.pos()[2:]} after {depth + 1} steps "
                             f"({moves} moves, {len(seen)} states)")
                    return {"found": True, "state": state,
                            "steps": depth + 1, "visited": len(seen),
                            "cells": set(seen)}
                if out == "blocked":
                    continue
                key = (self.map_name(),) + self.pos()[2:]
                if key in seen or key[0] in forbid:
                    continue
                seen.add(key)
                q.append((self._explore_snap(), depth + 1))
        self._explore_restore(root)
        log.info(f"  explore_bfs: no goal within budget "
                 f"({moves} moves, {len(seen)} states)")
        return {"found": False, "state": None, "steps": 0,
                "visited": len(seen), "cells": set(seen)}

    def _standable(self, name, c):
        """Path-existence is not enough: cross-map BFS treats any goal as
        reachable (warp tiles, counters). Standing spots must be real."""
        try:
            grid = self.nav.grid(name)
            if 0 <= c[0] < len(grid[0]) and 0 <= c[1] < len(grid):
                b = grid[c[1]][c[0]]
                from crystalagent.nav import WATER
                return b in WALKABLE or b in HOPS or \
                    (self.nav.surf and b in WATER)
            return False
        except KeyError:
            return False

    def enable_surf(self):
        """Turn on water routing once someone in the party knows SURF.
        The land->water step pops 'The water is calm... SURF?' -- goto's
        blocked-step handler flushes it (A = YES) and replans, so no other
        machinery changes. Verifies the party actually knows the move."""
        for mon in self.observe()["party"]:
            if any(m.get("name") == "SURF" for m in mon.get("moves", [])):
                self.nav.surf = True
                log.info("  [surf] water routing enabled")
                return True
        raise RuntimeError("enable_surf: nobody in the party knows SURF")

    def _approach_cells(self, x, y):
        """Every cell worth standing on to talk to (x,y), best first: the
        one already occupied, then adjacent walkable cells by PATH length,
        then (counters!) two cells out along a ray whose middle is blocked.

        A ranked LIST, not one pick, because the first choice can lose a
        race: Ilex Forest's Farfetch'd sat one cell above the player while
        the pick was its far side, the walk there was blocked by the bird
        itself, and `talk_to` gave up with a cell it never needed. The old
        fixed U/D/L/R scan had the same shape at Violet Gym's Abe."""
        here = self.pos()[2:]
        name = self.map_name()
        npcs = self.npc_cells()
        grid = self.nav.grid(name)
        wid, hgt = len(grid[0]), len(grid)
        out = []
        sides = [(x + dx, y + dy)
                 for dx, dy in ((0, -1), (0, 1), (-1, 0), (1, 0))]
        if here in sides and self._standable(name, here):
            out.append(here)
        ranked = []
        for c in sides:
            if c in out or not self._standable(name, c):
                continue
            path = self.nav.find_path(name, here, c, npcs)
            if path is not None:
                ranked.append((len(path), c))
        out += [c for _n, c in sorted(ranked, key=lambda p: p[0])]
        for dx, dy in ((0, -1), (0, 1), (-1, 0), (1, 0)):
            mid, far = (x + dx, y + dy), (x + 2 * dx, y + 2 * dy)
            if not (0 <= far[0] < wid and 0 <= far[1] < hgt):
                continue
            if mid in npcs or grid[mid[1]][mid[0]] in WARPS:
                continue   # would need to pass an NPC or a warp
            if far not in out and self._standable(name, far) and \
                    self.nav.find_path(name, here, far, npcs) is not None:
                out.append(far)
        return out

    def _approach_cell(self, x, y):
        """Best single cell to talk to (x,y) from, or None."""
        cells = self._approach_cells(x, y)
        return cells[0] if cells else None

    def _live_target(self, x, y, radius=2):
        """Where the NPC listed at (x,y) is standing RIGHT NOW.

        `maps/*.asm` gives an object_event's SPAWN cell, but a
        SPRITEMOVEDATA_WALK_* / WANDER object drifts from it. Approaching
        the spawn cell then talks to empty ground. When the spawn cell
        holds no live sprite and exactly one sits within `radius`, that
        one is the target; ambiguity (two candidates) keeps the caller's
        coordinates, which is the conservative answer."""
        try:
            live = self.npc_cells()
        except Exception:
            return x, y
        if (x, y) in live or not live:
            return x, y
        near = [c for c in live
                if abs(c[0] - x) + abs(c[1] - y) <= radius]
        if len(near) != 1:
            return x, y
        cell = near[0]
        log.info(f"  target moved: ({x},{y}) -> {cell}")
        return cell

    def talk_to(self, x, y, label="", facing=None):
        """Walk next to the NPC at (x,y) (or across a counter from them),
        face them, and talk. Fights any trainer battle that triggers
        (sight-lines are slow: polls for wBattleMode after the dialog).
        `facing` ('U'/'D'/'L'/'R') forces which way the player faces when
        talking (i.e. which side to approach from) -- some scripts branch
        on VAR_FACING (e.g. the Ilex Farfetch'd chase sends the bird
        BACKWARD on the wrong facing). Returns 'battle' | 'talked' | False."""
        if self.battle():
            self.fight()
            if self._whiteout_stop(f"talk_to ({x},{y})"):
                return False
        self.settle()
        x, y = self._live_target(x, y)
        if facing:
            fdx, fdy = STEP[facing]
            spot = (x - fdx, y - fdy)     # stand opposite the facing dir
            if not self._standable(self.map_name(), spot):
                log.info(f"  facing={facing} spot {spot} not standable")
                return False
            spots = [spot]
        else:
            spots = self._approach_cells(x, y)
        if not spots:
            log.info(f"  no approach to ({x},{y})")
            return False
        # A chosen side can lose a race with a wanderer (or with the very
        # NPC being addressed): walk to the next candidate instead of
        # reporting failure with three untried sides left. But a GLOBAL
        # blocker -- an open choice box, a naming keyboard, a wipe -- is
        # not about the side, and retrying seven more of them multiplies
        # the storm (live: Route 34's phone-number YES/NO turned five
        # trainer talks into forty replan storms).
        for spot in spots:
            if self.goto(*spot, label or f"approach ({x},{y})"):
                break
            reason = self.last_goto_reason or ""
            if any(k in reason for k in ("choice menu", "naming-keyboard",
                                         "whiteout", "battle during")):
                log.info(f"  approach {spot} blocked by {reason} -- that is "
                         f"not side-specific, stopping")
                return False
            log.info(f"  approach {spot} failed ({reason}); "
                     f"trying the next side")
        else:
            return False
        fdx = (x > spot[0]) - (x < spot[0])
        fdy = (y > spot[1]) - (y < spot[1])
        facing = {(-1, 0): "L", (1, 0): "R", (0, -1): "U", (0, 1): "D"}[
            (fdx, fdy)]
        self.step_dir(facing)          # blocked step = turn toward the NPC
        self.press("A:2 .:20")
        # Did the A press actually open anything? A wanderer that stepped
        # away leaves an empty cell, and pressing A at grass answered
        # 'talked' with no dialog at all -- Chuck's wife held HM02 FLY
        # through three such "successes" (journal #93).
        # (guarded: reduced/duck-typed drivers model neither menus nor
        # wScriptMode -- an unreadable probe must never invent silence)
        def _probe(fn, *a):
            try:
                return bool(fn(*a))
            except Exception:
                return True
        spoke = _probe(self.textbox) or _probe(self.menu_open) or \
            _probe(lambda: self.emu.read_u8("wScriptMode") != 0)
        outcome = self.flush_dialog(30000)
        # trainer triggers land slowly; poll before declaring it plain talk
        f0 = self.emu.frame
        while not self.battle() and self.emu.frame - f0 < 2400:
            self.press(".:60")
        if self.battle() or outcome == "battle":
            self.fight()
            if self._whiteout_stop(f"talk_to ({x},{y})"):
                return False
            # The NPC's script CONTINUES after the battle, and its tail is
            # the payoff: Falkner's ZEPHYR badge + TM31, Sage Li's HM05,
            # every gift trainer. Nothing pressed through it, so talk_to
            # returned 'battle' with the reward still pending and the
            # caller had to know to talk a second time (live: the badge
            # only arrived on the retry). flush_dialog stops on a choice
            # box, so this cannot blind-answer one (gotcha 13).
            self.settle()
            self.flush_dialog(8000)
            return "battle"
        if not spoke and not self.textbox():
            log.info(f"  nothing answered at ({x},{y}) -- no dialog opened")
            return False
        return "talked"
