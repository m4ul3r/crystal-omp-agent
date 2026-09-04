"""Driver input, menus, text, choices, and naming UI."""

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

# Naming-keyboard grids (9 cols x 4 char rows), parsed from
# data/text/name_input_chars.asm: each cell is 2 chars, space = empty key.
# Row 4 is controls: cols 0-2 case switch, 3-5 DEL, 6-8 END.
def _parse_name_grid(repo):
    import re as _re
    from pathlib import Path as _Path
    tables = {}
    text = (_Path(repo) / "data/text/name_input_chars.asm").read_text()
    for name in ("NameInputUpper", "NameInputLower"):
        m = _re.search(name + r":\n((?:\tdb \"[^\"]*\"\n)+)", text)
        rows = _re.findall(r'db "([^"]*)"', m.group(1))
        grid = {}
        for y, row in enumerate(rows[:4]):
            for x in range(9):
                ch = row[x * 2]
                if ch != " ":
                    grid[ch] = (x, y)
        tables[name] = grid
    return tables


NAME_GRIDS = None
_BATTLE_SHIFT_BIT = 6


class UIMixin:
    """Owns Driver input, menu, textbox, choice, and naming behavior."""
    def _menu_fail(self, reason):
        """Record why a menu primitive answered False, and say so once.

        Mirrors _item_fail without touching the UI: these are the inner
        primitives, and the caller (use_item, teach_tm) owns the exit."""
        self.last_menu_reason = reason
        log.info(f"  menu: {reason}")
        return False

    def _confirm_label(self, label, expect, **kw):
        """``Menus.select_label`` with the reached-state check, tolerating
        older or duck-typed Menus objects that predate `expect`.

        The fallback does the SAME verification here rather than trusting
        a cursor-glyph success -- that trust is the bug (gotcha 2: the A
        pressed on the frame a menu is drawn is swallowed)."""
        try:
            return self.menu.select_label(label, expect=expect, **kw)
        except TypeError:
            pass
        if not self.menu.select_label(label, **kw):
            why = getattr(self.menu, "last_reason", None)
            return self._menu_fail(
                f"select_label({label}): row not confirmed"
                + (f"; {why}" if why else ""))
        for _ in range(3):
            if expect(self.emu.screen_text()):
                return True
            self.press("A:2 .:10")
            self.press(".:12")
        return self._menu_fail(
            f"select_label({label}): state not reached after 3 confirms")

    def textbox(self):
        return self.emu.tilemap()[12 * 20] == 0x79

    def cursor_rows(self):
        """Stripped upper-case screen rows that carry a menu cursor glyph."""
        return [r.strip().upper() for r in self.emu.screen_text()
                if ("▶" in r or "▷" in r)]

    def menu_open(self):
        """Is anything modal on screen (menu cursor or textbox)? The
        overworld draws neither, so this is the 'am I interactive' check
        every menu primitive must pass before returning (gotcha 7: a stray
        START menu silently eats all movement input)."""
        return bool(self.textbox()) or \
            any("▶" in r or "▷" in r for r in self.emu.screen_text())

    def scene_busy(self):
        """True while any scene owns the world: a script is running
        (wScriptMode != 0), a box/textbox is up, or a naming screen is
        open. wScriptMode alone LIES -- it reads 0 during naming screens
        and lags through chains (omp-fresh addendum #3); gate drains on
        this, not on sm == 0."""
        try:
            sm = self.emu.read_u8("wScriptMode")
        except Exception:
            sm = 0
        return bool(sm) or self.menu_open() or self.keyboard_open()

    def _screen_blank(self):
        """Menu open/close transitions render a frame or two of nothing;
        judging menu state on one is a lie (the menu redraws right after)."""
        return sum(1 for r in self.emu.screen_text() if r.strip()) < 2

    def close_menus(self, max_presses=14):
        """Postcondition helper: B out of any open menu/textbox stack until
        the overworld is interactive again. Blank fade frames are waited
        out, never judged, and 'closed' must hold on a settled re-check
        (the pack repaints ~50 frames after its close fade). Returns True
        when clean."""
        for _ in range(max_presses):
            if self._screen_blank():
                self.press(".:30")
                continue
            if not self.menu_open():
                self.press(".:40")            # outlast a pending repaint
                if not self.menu_open() and not self._screen_blank():
                    return True
                continue
            self.press("B:4 .:20")
        return not self.menu_open() and not self._screen_blank()

    def press(self, seq):
        # naming-screen freeze (moss-run postmortem): while a keyboard is
        # up, ONLY explicit type_name/dismiss_keyboard may type -- any
        # other A/B/START press would insert chars or commit garbage.
        if self.keyboard_open() and not getattr(self, "_naming_busy", False):
            seq = ",".join(t for t in seq.split()
                           if t.upper().startswith((".:", "D:")))
            if not seq:
                return
        self.emu.run_sequence(parse_sequence(seq))

    def keyboard_open(self):
        s = self.emu.screen_text()
        return any("DEL" in r for r in s) and any("END" in r for r in s)

    @staticmethod
    def _choice_box(rows):
        """Geometry of an open choice box, or None when no cursor glyph is
        on screen: {'cursor': row index, 'options': [(row index, label)],
        'span': (left, right)}.

        A box drawn OVER the overworld shares its rows with map art
        ('▃▄◖▛▛◪▃▄▂▂▂▂λλ│ YES│'), so labels are read from the BOX's own
        column span -- the vertical bars either side of the cursor --
        never from the whole row. Reading whole rows made mom's
        day-picker unanswerable: 'YES' never equalled the decoded
        option, so `travel` died in PLAYERS_HOUSE_1F."""
        idx = next((i for i, r in enumerate(rows)
                    if any(c in r for c in CURSORS)), None)
        if idx is None:
            return None
        bars = "│┃"
        row = rows[idx]
        cx = min(row.index(c) for c in CURSORS if c in row)
        left = max((i for i, ch in enumerate(row[:cx]) if ch in bars),
                   default=-1)
        right = next((i for i in range(cx + 1, len(row))
                      if row[i] in bars), len(row))
        opts = []
        for j in range(max(0, idx - 3), min(len(rows), idx + 4)):
            t = rows[j][left + 1:right]
            for c in CURSORS:
                t = t.replace(c, "")
            t = t.strip().strip("│┃").strip()
            # a box overlapping blank map tiles decodes them as 'λλλλ'
            # (Greek is alnum!): a real option carries ASCII text
            if t and "─" not in t and "┌" not in t and "└" not in t \
                    and any(ch.isalnum() and ch.isascii() for ch in t):
                opts.append((j, t))
        return {"cursor": idx, "options": opts, "span": (left, right)}

    @classmethod
    def _choice_labels(cls, rows):
        """Options of an open choice box ('│▶YES│'/'│ NO │' ->
        ['YES','NO']); empty when no cursor glyph is on screen."""
        box = cls._choice_box(rows)
        return [t for _, t in box["options"]] if box else []

    def resolve_choice(self, choice="YES"):
        """Deliberately answer an open choice box: verify `choice` is
        visible on screen, navigate the cursor onto it, confirm. The
        caller owns semantics; this executes precisely instead of
        blind-mashing -- the gotcha-13 counterpart deciders were
        missing (R29 tutorial, nurse prompts, mom's day-picker).
        Returns {'answered': bool, 'chose': str|None, 'options': [...]}."""
        # scenes open with STORY PAGES before the box (aide monologue):
        # page them out glyph-gated first, then classify what's left.
        fr = self.flush_dialog(max_frames=3000)
        if fr == "battle":
            return {"answered": False, "chose": None, "options": [],
                    "note": "battle started"}
        f0 = self.emu.frame
        while self.emu.frame - f0 < 90:
            rows = self.emu.screen_text()
            if any(c in r for r in rows for c in CURSORS):
                break
            self.emu.tick(6)
        box = self._choice_box(self.emu.screen_text())
        opts = [t for _, t in box["options"]] if box else []
        if choice not in opts:
            return {"answered": False, "chose": None, "options": opts,
                    "note": "no choice cursor settled on screen"}
        # gotcha-2 variant: the box may still be settling when labels
        # first decode -- confirm-then-verify, one bounded retry
        for _attempt in range(2):
            self.press(".:12")
            if not self._point_at_choice(choice):
                opts = self._choice_labels(self.emu.screen_text()) or opts
                continue
            self.press("A:2 .:12")
            self.emu.tick(20)
            still = self._choice_labels(self.emu.screen_text())
            if choice not in still:
                return {"answered": True, "chose": choice, "options": opts}
            opts = still or opts
        return {"answered": False, "chose": None, "options": opts,
                "rows": [r.strip() for r in self.emu.screen_text()
                         if r.strip()][:8]}

    def _point_at_choice(self, choice, max_presses=8):
        """Walk the cursor onto `choice` inside an open choice box, UP or
        DOWN as the geometry demands. A YES/NO box does NOT wrap -- with
        the cursor defaulted onto NO, a DOWN-only walk (`select_label`)
        can never reach YES, which is exactly how mom's day-picker
        stalled every fresh game."""
        for _ in range(max_presses + 1):
            box = self._choice_box(self.emu.screen_text())
            if box is None:
                return False
            here = next((t for j, t in box["options"]
                         if j == box["cursor"]), None)
            if here == choice:
                return True
            target = next((j for j, t in box["options"] if t == choice),
                          None)
            if target is None:
                return False
            self.press(("U" if target < box["cursor"] else "D") + ":2 .:8")
        return False

    def who_fights(self):
        """Rank the party against the CURRENT battle's foe using the repo
        type chart (registry 'who_fights'; needs ui.battle). Switch
        decisions become evidence-based: best-move effectiveness per mon,
        healthiest and hardest-hitting first. Returns {'enemy': ...,
        'ranking': [...]} -- pair with fight(policy=('switch', slot))."""
        if not self.battle():
            raise ValueError("who_fights: needs an active battle "
                             "(ui.battle=False)")
        b = Battle(self.emu, self.names, self.bdata)
        enemy = b.enemy()
        tnames = {i: n for n, i in self.bdata.types.items()}
        etypes = enemy.get("types") or []
        move_id = {n: i for i, n in self.names.moves.items()}
        rows = []
        for i, m in enumerate(game_state(self.emu, self.names)["party"]):
            if m.get("egg"):
                continue
            best_eff, best_mv = 0.0, None
            for mv in m["moves"]:
                mid = move_id.get(mv["name"])
                if not mid:
                    continue
                mtype = self.bdata.moves[mid]["type"]
                eff = self.bdata.effectiveness(mtype, etypes)
                if eff > best_eff:
                    best_eff, best_mv = eff, mv["name"]
            rows.append({"slot": i, "mon": m.get("nickname") or m["name"],
                         "level": m["level"],
                         "hp": round(m["hp"] / max(m["max_hp"], 1), 2),
                         "best_move": best_mv, "eff": best_eff})
        rows.sort(key=lambda r: (-r["eff"], -r["level"]))
        return {"enemy": {"name": enemy["name"],
                          "types": [tnames.get(t, str(t)) for t in etypes],
                          "level": enemy["level"], "hp": enemy["hp"],
                          "max_hp": enemy["max_hp"]},
                "ranking": rows,
                "note": "send the top healthy ranked mon in via "
                        "fight(policy=('switch', slot))"}

    def gym_scout(self, map):
        """Read the repo's ground truth for a gym BEFORE entering:
        parse maps/<Map>.asm trainer references + data/trainers/parties.asm
        into [{trainer, group, mons: [{species, level, moves}]}] so roster
        evolution is planned, not discovered by wiping (repo-is-the-map).
        map: CONST ('VIOLET_GYM') or CamelCase."""
        const = self._resolve_map(map)
        path = paths.REPO_ROOT / "maps" / f"{const.title().replace('_', '')}.asm"
        if not path.exists():
            raise ValueError(f"gym_scout: no map source at {path}")
        text = path.read_text()
        wanted = []                       # (GROUP, TEMPLATE) pairs
        for m in re.finditer(r"loadtrainer\s+(\w+),\s*(\w+)", text):
            wanted.append((m.group(1), m.group(2)))
        for m in re.finditer(r"^\ttrainer\s+(\w+),\s*(\w+),", text, re.M):
            wanted.append((m.group(1), m.group(2)))
        if not wanted:
            raise ValueError(f"gym_scout: no trainers found in {const}")
        parties_path = paths.REPO_ROOT / "data/trainers/parties.asm"
        ptext = parties_path.read_text()
        out = []
        for group, template in wanted:
            camel = "".join(p.capitalize() for p in group.split("_"))
            gsec = re.search(
                rf"^({camel}Group:.*?)(?=^\w+Group:|\Z)",
                ptext, re.M | re.S)
            if not gsec:
                out.append({"trainer": template, "group": group,
                            "mons": [], "error": "group not in parties.asm"})
                continue
            base = re.sub(r"\d+$", "", template)

            def _norm(s):
                # 'AMYANDMAY1' vs parties 'AMY & MAY@': drop non-letters,
                # then the literal AND, from BOTH sides
                return re.sub(r"[^A-Z]", "", s.upper()).replace("AND", "")

            variants = {_norm(base), _norm(base.split("_")[-1])}

            def _is_template(line_name):
                cand = _norm(line_name)
                return any(cand == v for v in variants)

            tmatch = None
            for m in re.finditer(
                    r'db "([^"]+)@".*?\n((?:\s+db .*\n)+?)\s+db -1',
                    gsec.group(1)):
                if _is_template(m.group(1)):
                    tmatch = m
                    break
            if not tmatch:
                out.append({"trainer": base, "group": group,
                            "mons": [], "error": "template not found"})
                continue
            mons = []
            for line in tmatch.group(2).splitlines():
                fields = [f.strip() for f in line.strip().removeprefix("db").split(",")]
                if len(fields) < 2 or not fields[0].isdigit():
                    continue
                mon = {"level": int(fields[0]), "species": fields[1]}
                if "MOVES" in tmatch.group(0):
                    mon["moves"] = [f for f in fields[2:]
                                    if f and f != "NO_MOVE"]
                mons.append(mon)
            out.append({"trainer": base, "group": group, "mons": mons})
        return out

    def _naming_sig(self):
        """WRAM signature of naming-screen state; NamingScreen writes
        these BEFORE rendering (engine/menus/naming_screen.asm), so a
        delta beats every screen-text check on fade-in frames."""
        e = self.emu
        return (e.read_u8("wNamingScreenType"),
                e.read_u8("wNamingScreenDestinationPointer"))

    def _naming_screen_plausible(self):
        """True when the naming-screen WRAM union holds values a real
        NamingScreen call could have written. Those bytes ($c6d0-$c6d8)
        are UNIONED with other screen buffers, so a cutscene scribbling
        tilemap data through them moves _naming_sig() and used to be read
        as 'a keyboard opened' -- 30+ wasted B/START/A presses per scene,
        which is how a blind A press walked into the START menu and
        browsed the Pokedex mid-cutscene.

        wNamingScreenType is masked with NUM_NAMING_SCREEN_TYPES
        (constants/menu_constants.asm:129 -> 8 types) and the longest name
        the game ever asks for is 10 (a mon nickname), so anything outside
        those ranges is somebody else's data."""
        e = self.emu
        return (e.read_u8("wNamingScreenType") < 8
                and 1 <= e.read_u8("wNamingScreenMaxNameLength") <= 10
                and e.read_u8("wNamingScreenCurNameLength") <= 10)

    def _naming_opened(self, sig0):
        """Is a naming keyboard REALLY up? A WRAM signature delta alone is
        a guess (the bytes are unioned); a rendered DEL/END row is proof.
        On a delta we therefore wait briefly for the render and, if it
        never arrives, report False -- confirming a keyboard that is not
        there types START+A into the overworld, which opened the START
        menu and walked into the Pokedex twice this session. A real
        keyboard is patient: waiting costs nothing."""
        if self.keyboard_open():
            return True
        if self._naming_sig() == sig0 or not self._naming_screen_plausible():
            return False
        for _ in range(8):                       # ~80 frames of grace
            self.emu.tick(10)
            if self.keyboard_open():
                return True
        return False

    BATTLE_SHIFT_BIT = 6   # constants/ram_constants.asm: set = "SET" style

    @staticmethod
    def _text_speed_byte(opts, mode):
        """wOptions low TEXT_DELAY_MASK bits select render delay
        (FAST=%001, MED=%011, SLOW=%101); upper option bits survive --
        except BATTLE_SHIFT, which is forced to SET: the SHIFT-style
        "<trainer> is about to use X. Will you change POKeMON?" YES/NO
        lands between two blind text presses, the A that pages the KO
        text answers YES, and the "Which PKMN?" list that follows loops
        SWITCH -> "already out" (or "An EGG can't battle!") until the
        frame cap. No caller ever wanted that prompt."""
        delays = {"FAST": 0b001, "MED": 0b011, "SLOW": 0b101}
        return (opts & ~0b111) | delays[mode] | (1 << _BATTLE_SHIFT_BIT)

    def set_text_speed(self, mode="FAST"):
        """Force fast text rendering: pages complete in fewer frames so
        drains stop paying the per-press tax (moss-run [W]: Elm speech
        cost 104 A presses on the default speed). Cheap + idempotent --
        safe to call on every drain entry; new-game resets re-apply."""
        try:
            self.emu.write("wOptions",
                           self._text_speed_byte(
                               self.emu.read_u8("wOptions"), mode))
            return True
        except Exception:
            return False

    def name_prompt(self, name):
        """Registry 'name_prompt': give a DELIBERATE name on whatever
        naming keyboard is currently open (hatch prompts, catch naming).
        The press() freeze blocks every other input source while this
        runs, so persona names land exactly once. Precondition: a naming
        screen must be up (keyboard_open)."""
        if not self.keyboard_open():
            raise ValueError(
                "name_prompt: no naming keyboard open -- poll "
                "keyboard_open() after hatches/catches first")
        self.dismiss_keyboard(name)

    def _take_pending_nickname(self):
        """Resolve the naming screen that just opened, consuming
        `_pending_nickname` if one is armed (gift mons: the starter,
        Togepi, Eevee, the Odd Egg's hatch). One-shot: the name never
        leaks into the next prompt."""
        name = self._pending_nickname
        if callable(name) or isinstance(name, dict):
            name = None       # species-keyed forms need a species; gifts
        self._pending_nickname = None
        self.dismiss_keyboard(name)
        return name

    def dismiss_keyboard(self, name=None):
        """Confirm a naming screen. With a name, actually type it; without,
        confirm with the minimal name (fast path). Runs with the naming
        freeze lifted -- this is the ONLY sanctioned typer."""
        was = getattr(self, "_naming_busy", False)
        self._naming_busy = True
        try:
            if name:
                log.info(f"  naming keyboard: typing {name!r}")
                for _ in range(12):   # B = backspace: clear stray chars
                    self.press("B:3 .:10")
                self.type_name(name)
                return
            log.info("  naming keyboard: confirming")
            for _ in range(12):       # clear strays so decline is clean
                self.press("B:3 .:10")
            self.press("START:4 .:20 A:4 .:30")          # END + confirm
            if self.keyboard_open():                  # empty refused:
                self.press("A:2 .:10 START:4 .:20 A:4 .:30")  # 1 letter
        finally:
            self._naming_busy = was

    def type_name(self, name, max_len=10):
        """Type `name` on the naming keyboard (uppercase only -- the game
        renders names in caps anyway). Runs with the freeze lifted."""
        was = getattr(self, "_naming_busy", False)
        self._naming_busy = True
        # The naming window SLIDES IN: the letter grid is on screen for
        # ~40 frames before DEL/END are drawn and the joypad loop reads
        # input. Typing into that animation silently drops every press --
        # a Cyndaquil got handed over as "CYNDAQUIL" that way. Wait for a
        # fully drawn keyboard first (gotcha 2, the naming-screen case).
        for _ in range(40):
            if self.keyboard_open():
                break
            self.emu.tick(10)
        else:
            log.warning("  type_name: keyboard never finished drawing "
                        "(no DEL/END row) -- typing anyway")
        global NAME_GRIDS
        if NAME_GRIDS is None:
            NAME_GRIDS = _parse_name_grid(paths.REPO_ROOT)
        grid = NAME_GRIDS["NameInputUpper"]

        def kb_cursor():
            p = self.emu.read("wNamingScreenCursorObjectPointer", 2)
            ptr = p[0] | (p[1] << 8)
            st = self.emu.read((1, ptr), 14)
            return st[12], st[13]

        def kb_step(btn, want):
            for _ in range(5):
                self.press(f"{btn}:8 .:16")
                if kb_cursor() == want:
                    return want
            return kb_cursor()

        def name_len():
            return self.emu.read_u8("wNamingScreenCurNameLength")

        chars = [c for c in name.upper()[:max_len] if c in grid]
        if not chars:
            chars = ["A"]
        log.info(f"  typing name {''.join(chars)!r}")
        self.press("START:6 .:20")               # snap to END zone (8,4)
        x, y = kb_step("U", (8, 3))              # control row moves by ZONE,
        for ch in chars:                         # so navigate on char rows
            tx, ty = grid[ch]
            for _ in range(12):                  # horizontal first
                if x == tx:
                    break
                x, y = kb_step("R" if tx > x else "L",
                               (x + (1 if tx > x else -1), y))
            for _ in range(6):                   # then vertical
                if y == ty:
                    break
                x, y = kb_step("D" if ty > y else "U",
                               (x, y + (1 if ty > y else -1)))
            before = name_len()
            for _ in range(3):                   # A adds the character
                self.press("A:8 .:16")
                if name_len() > before or name_len() >= max_len:
                    break
        self.press("START:6 .:20 A:10 .:40")     # snap to END, confirm
        self._naming_busy = was

    def _flush_dialog_hooks(self, max_frames, quiet_frames=40):
        """Event-driven advance: A only while the engine reports a page
        waiting for a button (PromptButton hook); stop the moment a menu
        or battle-end event fires -- zero blind presses."""
        f0, quiet = self.emu.frame, 0
        sig0 = self._naming_sig()
        self.set_text_speed()
        while self.emu.frame - f0 < max_frames:
            if self.battle():
                return "battle"
            if self._naming_opened(sig0):
                # A GIFT mon (starter, Togepi, Eevee...) also opens the
                # naming keyboard, and this path used to always confirm
                # empty -- the persona's PANIC came out of Elm's lab
                # called CYNDAQUIL twice. Honour _pending_nickname here
                # too, exactly like fight()/catch() do.
                self._take_pending_nickname()
                quiet = 0
                continue
            sig0 = self._naming_sig()   # re-baseline: no keyboard came up
            events = self.hooks.drain()
            kinds = [k for k, _ in events]
            if hookevents._STOP_EVENTS & set(kinds):
                self.press(".:12")
                return "menu"
            if "page_wait" in kinds and dialog_press_safe(
                    self.emu.screen_text()):
                self.press("A:2 .:8")
                quiet = 0
                continue
            self.press(".:8")
            quiet += 8
            if quiet >= quiet_frames:
                # A page_wait event is consumed on drain even when it
                # could not be acted on (stale/mid-transition), after
                # which the loop goes deaf while the box keeps waiting.
                # The visible textbox is the persistent signal: fall
                # back to glyph-gated paging instead of reporting done.
                if self.textbox():
                    if dialog_press_safe(self.emu.screen_text()):
                        self.press("A:2 .:8")
                        quiet = 0
                        continue
                    self.last_choice_options = \
                        self._choice_labels(self.emu.screen_text())
                    return "menu"   # cursor outside box: deliberate
                return "done"
        return "timeout"

    def flush_dialog(self, max_frames=6000, quiet_frames=40):
        """Press A while a textbox is up; return once it's been gone a
        bit. Handles a naming keyboard if one appears. With live hooks
        this is event-driven; otherwise the legacy cadence applies,
        gated by dialog_press_safe and a naming-screen WRAM delta so a
        fade-in keyboard never eats A presses as keystrokes."""
        f0, quiet = self.emu.frame, 0
        sig0 = self._naming_sig()
        self.set_text_speed()
        while self.emu.frame - f0 < max_frames:
            if self.battle():
                return "battle"
            if self.hooks is not None and \
                    self.hooks.has("page_wait"):
                return self._flush_dialog_hooks(max_frames, quiet_frames)
            rows = self.emu.screen_text()
            if self._naming_opened(sig0):
                self._take_pending_nickname()
                quiet = 0
            elif self._naming_sig() != sig0:
                sig0 = self._naming_sig()   # false alarm; re-baseline
            elif self.textbox() and dialog_press_safe(rows):
                self.press("A:2 .:8")
                quiet = 0
            elif self.textbox():
                # cursor glyph outside the box: a choice/menu opened --
                # report instead of blind-picking it (AGENTS.md gotcha 13)
                self.last_choice_options = self._choice_labels(rows)
                return "menu"
            else:
                self.press(".:8")
                quiet += 8
                if quiet >= quiet_frames:
                    return "done"
        return "timeout"

    def _drain_scene(self, max_pages=25, max_frames=6000):
        """Bounded auto-drain for a scripted scene blocking movement
        (Elm's phone call, the rival ambush, aide hand-offs): page
        through with A until the textbox is gone AND wScriptMode reads
        0, so the blocked step can be retried instead of replan-storming
        (the top friction of the claude-wren run). Movement phases
        between pages (applymovement) are waited out, never pressed
        into. Never mashes a choice menu -- but only an ACTUAL cursor
        glyph ($ec '▷' / $ed '▶') on screen is a menu (gotcha 13); a
        drawn-but-EMPTY textbox is a still-rendering page (leg-2: 8
        false 'blocked by choice menu' aborts on blank pre-battle
        trainer boxes), so wait briefly and page it. Returns
        'done' | 'battle' | 'menu' | 'timeout'."""
        self.set_text_speed()
        pages = 0
        f0 = self.emu.frame
        while pages < max_pages and self.emu.frame - f0 < max_frames:
            if self.battle():
                return "battle"
            if self.textbox():
                rows = self.emu.screen_text()
                if not dialog_press_safe(rows):
                    # dialog_press_safe fails on TWO very different
                    # screens: a real choice box (cursor glyph drawn)
                    # and a textbox whose text hasn't rendered yet --
                    # trainer boxes draw the frame a beat before the
                    # text. Only a cursor is a menu; a blank box just
                    # needs a short bounded wait, then A is safe.
                    for i in range(7):
                        if any(c in r for r in rows for c in CURSORS):
                            self.last_choice_options = \
                                self._choice_labels(rows)
                            return "menu"   # true choice: never blind-pick
                        if dialog_press_safe(rows) or i == 6:
                            break
                        self.press(".:10")  # let the page render
                        rows = self.emu.screen_text()
                self.press("A:2 .:8")
                pages += 1
                continue
            try:
                if self.emu.read_u8("wScriptMode"):
                    self.press(".:20")  # scene still running its script
                    continue
            except Exception:
                pass
            self.settle(max_frames=300)  # let a follow-on page land
            if self.battle():
                return "battle"
            if not self.textbox():
                return "done"
        return "timeout"

    def drain_scene(self, max_frames=6000):
        """Public scene-exit primitive (registry 'drain_scene'): page a
        scripted scene until interactive, then B once if a residual box
        ignores A -- some scene-enders are A-deaf (omp-fresh's Elm call
        needed one B after 40 A presses). Choice boxes still surface as
        'menu' (gotcha 13): answer them deliberately."""
        r = self._drain_scene(max_frames=max_frames)
        if r in ("done", "timeout") and \
                (self.textbox() or self.menu_open()):
            self.press("B:4 .:16")
            r = self._drain_scene(max_frames=min(max_frames, 2000))
        return r

    def _wait_screen(self, pred, frames=500):
        """Tick (no input) until pred(uppercase screen text) is true."""
        n = 0
        while n < frames:
            if pred("".join(self.emu.screen_text()).upper()):
                return True
            self.emu.tick(10)
            n += 10
        return False

    def select_menu_row(self, label, max_presses=14, confirm=True,
                        match=None, confirm_seq="A:6 .:18"):
        """Text-targeted submenu/list selection (Menus.select_row_text):
        find the row whose text names `label` (or satisfies `match`),
        step the cursor exactly to it, verify after every press, then
        confirm. First-class because variable-layout submenus -- the
        party slot menu lists field moves ABOVE SWITCH -- and scrolled
        pack windows make positional press counts unsafe (wren pt6).
        The long default confirm press avoids the swallowed-A gotcha
        (START menu / pack, gotcha 2)."""
        fn = getattr(self.menu, "select_row_text", None)
        if fn is not None:
            return fn(label, max_presses=max_presses, confirm=confirm,
                      match=match, confirm_seq=confirm_seq)
        # duck-typed fakes / older Menus: best-effort select_label fallback
        legacy = getattr(self.menu, "select_label", None)
        if legacy is None:
            return False
        try:
            return legacy(label, max_presses=max_presses, confirm=confirm)
        except TypeError:
            try:
                return legacy(label, max_presses=max_presses)
            except TypeError:
                return legacy(label)
