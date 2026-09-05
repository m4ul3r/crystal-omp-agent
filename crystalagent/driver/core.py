"""Driver construction, lifecycle, persistence, and shared diagnostics."""

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

class CoreMixin:
    """Owns Driver construction, persistence, and shared lifecycle state."""
    def __init__(self, state_path=None, fresh=False, live=None):
        """fresh=True: power-on reset (no savestate loaded); `state_path` is
        then only the file a later save() writes to. Documented in AGENTS.md's
        capabilities map (`Driver(state, fresh=True)`) and used by
        scripts/newgame_bedroom.py.

        live={...}: attach a LiveFeed with those kwargs (name/fps/speed/
        state_hz/directory) so watch.py can show THIS emulator's frames.
        `live={}` takes the defaults; `d.live_attach(**kw)` does the same
        after construction. Both AGENTS.md and HANDBOOK.md promised this and
        the kwarg did not exist -- every watched leg died on TypeError."""
        target = Path(state_path or paths.DEFAULT_STATE)
        sym = Symbols(paths.SYM)
        cm = Charmap(paths.CHARMAP)
        self.emu = Crystal(paths.ROM, sym, cm, None if fresh else target)
        self.state_path = target
        # Savestates can carry phantom held keys.
        self.emu.release_buttons(settle_frames=10)
        self.names = Names(paths.ROM, sym, cm, paths.MAP_CONSTANTS)
        self.nav = TrekNav(paths.REPO_ROOT)
        self.menu = Menus(self.emu)
        self.bdata = BattleData(paths.REPO_ROOT, sym, paths.ROM)
        self._pending_nickname = None
        self.last_choice_options = []   # labels of the last refused box
        self.auto_fight = True   # False: nav battles bubble to the decider
        self.encounter_events = []   # decision-transparency journal
        self._whiteout_pending = False   # set by fight() on a detected wipe
        self.whiteouts = 0
        self.whiteout_policy = "abort"   # 'abort' | 'continue' (old behavior)
        # Battle policy used by every fight() the driver starts on the
        # player's behalf (talk_to trainer intercepts, goto/travel/walk
        # encounter intercepts, use_cut, registry 'fight') when no
        # explicit policy is passed. Whitney lesson (wren pt3): talk_to
        # auto-fought the gym leader with the DEFAULT policy before a
        # custom one could attach -- set d.default_policy BEFORE the
        # approach. Explicit fight(policy=...) args always win.
        self.default_policy = None
        # Level-up learn transparency (wren pt4: the learn flow replaced
        # BITE with SCARY FACE and a slot-1 policy whiffed through three
        # whiteouts): every resolved learn flow that REPLACES a move
        # appends {'mon','forgot','learned','slot','source'} here and
        # logs a LEARN line ('source': 'policy' | 'auto' |
        # 'auto-fallback' -- who decided the sacrifice; see
        # _diff_learned_moves). Inspect after train()/fight() before trusting
        # slot-based policies. Never cleared automatically.
        self.move_changes = []
        self.hooks = hookevents.install(self.emu)
        self.live = None
        if live is not None:
            self.live_attach(**live)

    def _load_state(self, state_path):
        """Reload this Driver, retaining the old path on validation failure."""
        loaded = self.emu.load(state_path)
        self.state_path = loaded
        self.emu.release_buttons(settle_frames=10)
        return loaded

    def live_attach(self, **kw):
        """Publish this emulator's frames/state/log to `live/<name>.*` for
        watch.py; returns the LiveFeed. The DRIVING emulator does the
        rendering (inside emu.tick's slices), so the viewer never has to
        re-simulate a savestate and can show the title screen, Oak's
        speech and the naming keyboard -- none of which is ever saved.

        `name` defaults to the working state's stem. Idempotent: a second
        call detaches the previous feed first, and an atexit hook detaches
        the last one -- a narration handler still attached at interpreter
        shutdown writes to closed streams and prints `Error in
        sys.excepthook` three times after an otherwise clean leg."""
        import atexit
        from crystalagent.live import LiveFeed
        if getattr(self, "live", None) is not None:
            self.live.detach()
        kw.setdefault("name", self.state_path.stem)
        self.live = LiveFeed(self.emu, self.names, self.nav, **kw).attach()
        atexit.register(self.live_detach)
        return self.live

    def live_detach(self):
        if getattr(self, "live", None) is not None:
            self.live.detach()
            self.live = None

    last_item_reason = None

    last_menu_reason = None

    last_step_reason = None

    last_tm_reason = None

    last_pc_reason = None

    last_field_reason = None

    last_money_delta = 0

    last_warp_reason = None

    last_battle = None

    last_frame = None

    last_goto_reason = None

    @staticmethod
    def _save_target(default_path, name):
        """Resolve a save target: bare milestone names land in saves/;
        path-like names (absolute or containing a directory component)
        are honored verbatim so sessions can isolate their checkpoints."""
        if not name:
            return default_path
        p = Path(name)
        return p if len(p.parts) > 1 else Path(paths.SAVES_DIR) / name

    def _save_blockers(self):
        """Names of everything that makes the CURRENT screen unsafe to
        bake into a savestate: a live battle, a running script, a textbox,
        or any menu cursor glyph ($ec '▷' / $ed '▶'). Empty list = clean
        interactable overworld."""
        blockers = []
        if self.battle():
            blockers.append("battle")
        try:
            sm = self.emu.read_u8("wScriptMode")
        except Exception:
            sm = 0
        if sm:
            blockers.append(f"running script (wScriptMode={sm})")
        if self.textbox():
            blockers.append("textbox")
        if any(c in r for r in self.emu.screen_text() for c in CURSORS):
            blockers.append("menu cursor")
        return blockers

    def save(self, name=None, force=False):
        """Save the working state (plus a `name` milestone copy when given).
        Refuses to overwrite a file whose .meta frame count is NEWER than
        the running emulation unless force=True -- the accidental-rollback
        class (older checkpoint over post-badge progress) now fails loudly
        inside the harness instead of silently regressing.

        Also refuses to bake a DIRTY screen into the state (wren pt3: a
        stuck pack layer saved into wren.state poisoned every fork made
        from it): the game must be a clean interactable overworld --
        wScriptMode 0, no textbox, no menu cursor, not in battle. Dirty
        screens get a bounded B-press auto-recovery first; force=True
        bypasses the check but LOGS what it is overriding."""
        if force:
            # force is legitimate (rolling back a fork on purpose), but it
            # must never be QUIET: a state baked with a menu open reloads
            # with dead movement, because the open menu eats every input
            # (AGENTS.md gotcha 7), and every fork made from it inherits it.
            blockers = self._save_blockers()
            if blockers:
                log.warning(
                    f"  saving OVER blockers ({', '.join(blockers)}) because "
                    f"force=True -- a reloaded state with an open menu has "
                    f"dead movement (gotcha 7)")
        if not force:
            # legit saves happen right AFTER dialogs: settle before the
            # first check so a closing box isn't judged mid-fade
            self.settle(max_frames=300)
            blockers = self._save_blockers()
            for _ in range(4):
                if not blockers or "battle" in blockers:
                    break                 # never B-mash inside a battle
                self.press("B:4 .:20")    # bounded auto-recovery
                self.settle(max_frames=300)
                blockers = self._save_blockers()
            if blockers:
                raise RuntimeError(
                    "refusing to save a dirty screen ("
                    + ", ".join(blockers)
                    + ") -- close it first or pass force=True")
        target = self._save_target(self.state_path, name)
        meta = Path(str(target) + ".meta")
        if meta.exists() and not force:
            try:
                old = json.loads(meta.read_text()).get("frames", 0)
            except Exception:
                old = 0
            if old > self.emu.frame:
                raise RuntimeError(
                    f"refusing to overwrite {meta.name} (frame {old}) with "
                    f"frame {self.emu.frame} -- pass force=True to roll back")
        self.emu.save(target)
        if name:  # also update the working state
            self.emu.save(self.state_path)
        log.info(f"[saved {target.name}] {self.status()}")
