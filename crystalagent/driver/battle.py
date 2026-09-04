"""Driver battle decisions, encounters, move learning, and training."""

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
                      cheapest_heal, goto_pocket, norm_item)
from ..charmap import Charmap
from ..decide import DecisionRequired, TurnLog, battle_frame
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
from .inventory import _item_row_matches, heal_pokecenter


log = logging.getLogger("trek")

def _policy_style(pol):
    """Which call shape a battle/encounter policy declares: 'frame' for
    the wren-pt6 single-argument policy(frame), 'legacy' for the historic
    policy(rows, me, enemy). Anything uninspectable (builtins) or
    *args-shaped is legacy: every policy written before pt6 takes the
    triple and a live kernel still holds some."""
    if pol is None:
        return "legacy"
    try:
        params = list(inspect.signature(pol).parameters.values())
    except (TypeError, ValueError):
        return "legacy"
    slots = 0
    for p in params:
        if p.kind is inspect.Parameter.VAR_POSITIONAL:
            return "legacy"
        if p.kind in (inspect.Parameter.POSITIONAL_ONLY,
                      inspect.Parameter.POSITIONAL_OR_KEYWORD):
            slots += 1
    return "frame" if slots == 1 else "legacy"


def _turn_row(rec, **row):
    """Append one turn and return the stored row for after-HP updates."""
    stored = rec.record(**row)
    return stored if isinstance(stored, dict) else row

class BattleMixin:
    """Owns Driver battle, policy, catch, learn, and training behavior."""
    auto_fight_steps = False

    encounter_policy = None

    decide_all = False

    def battle(self):
        return self.emu.read_u8("wBattleMode")

    FREE_HIT_LOUD = 2

    BALL_PREFERENCE = ("POKE BALL", "GREAT BALL", "ULTRA BALL")

    _tactics = None

    @property
    def tactics(self):
        """Type/damage analysis for this save (crystalagent.tactics).

        Badge-boosted attacking types are read live, because the boost is
        worth +1/8 damage and depends on which badges this file has
        (DoBadgeTypeBoosts, engine/battle/misc.asm:147). The ROM's heal
        table goes in too, so a mid-battle status cure names a real item
        at its real price instead of a guess."""
        if self._tactics is None:
            from crystalagent.tactics import Tactics, boosted_types
            self._tactics = Tactics(
                self.bdata, self.names, paths.REPO_ROOT,
                badge_types=boosted_types(self.emu, self.bdata,
                                          paths.REPO_ROOT),
                heal_table=self._heal_items())
        return self._tactics

    def outlook(self):
        """Real per-turn combat maths for the CURRENT battle, or None.

        Every one of my moves scored with the game's own damage formula
        against the enemy actually standing there -- type multiplier, the
        Gen-2 physical/special split (which is per TYPE), STAB, badge
        boost, the 85-100% spread, hits-to-KO -- plus the enemy's moves
        aimed back at me and who moves first. `d.tactics.explain(...)`
        renders it as one auditable line per move."""
        if not self.battle():
            return None
        return self.tactics.read(self.emu)

    def battle_frame(self):
        """The decision frame for the CURRENT battle -- the dict
        crystalagent.decide.battle_frame documents (me/enemy/party/bag/
        turn/wild/can_switch/moves) -- or None with no battle up (or no
        decide module). Exactly what encounter_policy and frame-shaped
        battle policies are handed, exposed so a model can read the same
        thing by hand instead of stitching game_state()/observe() together
        for every decision."""
        if not self.battle():
            return None
        return self._frame(Battle(self.emu, self.names, self.bdata))

    def _frame(self, b):
        """Return a live battle decision frame, or None when unreadable."""
        try:
            frame = battle_frame(b)
        except Exception as err:
            if not getattr(self, "_frame_warned", False):
                self._frame_warned = True
                log.warning(f"  [fight] battle_frame unavailable ({err}); "
                            f"policies get the legacy (rows, me, enemy)")
            return None
        self.last_frame = frame
        return frame

    @staticmethod
    def _ask(hook, frame, rows, me, enemy):
        """Call a decision hook in its declared frame or legacy shape."""
        if _policy_style(hook) == "frame":
            return hook(frame)
        return hook(rows, me, enemy)

    def _consult_encounter(self, b, policy, must_decide):
        """Ask self.encounter_policy ONCE, the moment a WILD appears, what
        to do with it: 'ko' | 'catch' | 'flee' | ('ball', NAME). Returns
        (disposition, per-turn policy): 'catch' and 'flee' REPLACE the
        per-turn policy for this battle, 'ko' (and no answer) keep it.
        Trainer battles never come here -- there is nothing to decide.

        A hook that raises, or answers with something outside the
        vocabulary, logs ONE warning and KOs: a bad hook never wedges a
        battle. With decide_all/require_decision set, NO answer is an
        error (DecisionRequired) rather than a silent auto-KO."""
        hook = getattr(self, "encounter_policy", None)
        if hook is None and not must_decide:
            # nothing to ask and nothing to refuse: don't pay for a frame
            return None, policy
        frame = self._frame(b)
        try:
            rows = self.emu.screen_text()
        except Exception:
            rows = []
        try:
            me, enemy = b.me(), b.enemy()
        except Exception:
            me, enemy = {}, {}
        who = enemy.get("name") if isinstance(enemy, dict) else None

        def unanswered(why):
            if must_decide:
                raise DecisionRequired(
                    f"wild {who}: {why} -- answer 'ko' | 'catch' | 'flee' "
                    f"| ('ball', NAME)", frame=frame, kind="encounter",
                    options=("ko", "catch", "flee", "ball"))
            return None, policy

        if hook is None:
            return unanswered("no encounter_policy set")
        try:
            disp = self._ask(hook, frame, rows, me, enemy)
        except DecisionRequired:
            raise
        except Exception as err:
            log.warning(f"  [encounter] encounter_policy raised ({err}); "
                        f"KO'ing wild {who}")
            return "ko", policy
        if disp is None:
            return unanswered("encounter_policy returned None")
        kind = disp[0] if isinstance(disp, tuple) and disp else disp
        kind = kind.strip().lower() if isinstance(kind, str) else kind
        ball = disp[1] if isinstance(disp, tuple) and len(disp) > 1 else None
        if kind == "flee":
            log.info(f"  [encounter] wild {who}: flee")
            return "flee", lambda rows, me, enemy: "flee"
        if kind in ("catch", "ball"):
            ball = self._encounter_ball(ball)
            log.info(f"  [encounter] wild {who}: catch with {ball}")
            return f"catch:{ball}", self._ball_policy(ball)
        if kind == "ko":
            log.info(f"  [encounter] wild {who}: KO")
            return "ko", policy
        log.warning(f"  [encounter] encounter_policy answered {disp!r}, want "
                    f"'ko' | 'catch' | 'flee' | ('ball', NAME); KO'ing "
                    f"wild {who}")
        return "ko", policy

    def _new_turn_log(self):
        return TurnLog()

    def _action_label(self, act, me=None):
        """One readable phrase for a battle decision ('attack slot 0
        (SURF)'), so a log line names the move actually used."""
        kind = act[0] if isinstance(act, tuple) and act else act
        arg = act[1] if isinstance(act, tuple) and len(act) > 1 else None
        if kind == "attack":
            if not isinstance(arg, int):
                return "attack (best move)"
            name = "?"
            try:
                moves = (me or {}).get("moves") or []
                if arg < len(moves):
                    mid = moves[arg][0]
                    name = self.names.moves.get(mid, f"?id{mid}")
            except Exception:
                pass
            return f"attack slot {arg} ({name})"
        if kind == "switch":
            return f"switch to party slot {arg}"
        if kind in ("ball", "item"):
            return f"{kind} {arg}"
        return str(kind)

    def _auto_action(self, b, me, enemy, state, steered):
        """What the harness would have played SILENTLY, resolved here (not
        inside Battle.play's own fallback) so the log line can name the
        exact slot and move that gets used. Logged ONCE per battle:
        WARNING when nothing at all was steering, INFO when a policy
        merely declined this turn."""
        act = "attack"
        try:
            act = b._default_policy(me, enemy, 0.3)
        except Exception:
            pass
        kind = act[0] if isinstance(act, tuple) and act else act
        arg = act[1] if isinstance(act, tuple) and len(act) > 1 else None
        if kind == "attack":
            # ALWAYS re-resolve the slot through best_move(): Battle's own
            # heuristic sometimes hands back slot 0, and slot 0 is a status
            # move for most parties (TACKLE-over-EMBER cost a Scyther
            # fight; GROWL/LEER cost two whiteouts -- #21/#24).
            try:
                slot = b.best_move()
            except Exception:
                slot = None
            if slot is not None:
                act = ("attack", slot)
            elif not isinstance(arg, int):
                act = ("attack", 0)
        if state["autos"] == 0:
            label = self._action_label(act, me)
            if steered:
                log.info(f"  [fight] auto: {label} (policy declined this "
                         f"turn)")
            else:
                log.warning(f"  [fight] auto: {label} -- no policy, no "
                            f"default_policy, decide_all off: the HARNESS "
                            f"is choosing this battle")
        state["autos"] += 1
        return act, "auto"

    @staticmethod
    def _close_turn(state, me, enemy):
        """Fill the PREVIOUS turn's after-HP from the vitals read at the
        start of this one: that difference is what a free hit costs."""
        row = state.get("last_row")
        if not isinstance(row, dict):
            return
        if isinstance(me, dict) and row.get("my_hp_after") is None:
            row["my_hp_after"] = me.get("hp")
        if isinstance(enemy, dict) and row.get("enemy_hp_after") is None:
            row["enemy_hp_after"] = enemy.get("hp")

    def _turn_policy(self, b, policy, must_decide, disposition=None):
        """Wrap the per-turn policy so EVERY turn lands on self.last_battle
        and the harness never picks invisibly. Returns (state, wrapped).

        The wrapped policy ALWAYS returns a concrete action, so Battle.play
        can no longer fall back to its best-damage picker behind our back:
        with must_decide the missing decision raises DecisionRequired
        (carrying the frame), otherwise the harness's own pick is resolved
        here and logged."""
        style = _policy_style(policy)
        rec = self._new_turn_log()
        self.last_battle = rec
        state = {"turns": 0, "free_hits": 0, "autos": 0, "last_row": None,
                 "disposition": disposition, "log": rec}

        def wrapped(rows, me, enemy):
            state["turns"] += 1
            self._close_turn(state, me, enemy)
            # a legacy policy that cannot be refused never looks at the
            # frame: don't re-read the party and bag every turn for it
            frame = (self._frame(b) if style == "frame" or must_decide
                     else None)
            act = None
            if policy is not None:
                try:
                    act = (policy(frame) if style == "frame"
                           else policy(rows, me, enemy))
                except Exception as err:
                    # A raising policy used to be indistinguishable from a
                    # policy that declined, and the fallback then played
                    # slot 0 -- silent status-move spam for whole battles
                    # (FUCK_I_MESSED_UP.md #21). Say it out loud.
                    log.error(f"  [fight] policy RAISED "
                              f"{type(err).__name__}: {err} -- falling back "
                              f"to the harness pick for this turn")
                    act = None
            source = "policy"
            if act is None:
                if must_decide:
                    why = ("policy returned None" if policy is not None
                           else "no policy set")
                    raise DecisionRequired(
                        f"turn {state['turns']}: {why} and this fight "
                        f"requires a decision -- answer ('attack', slot) | "
                        f"('switch', party_index) | ('item', NAME) | "
                        f"('ball', NAME) | 'flee'",
                        frame=frame, kind="turn",
                        options=("attack", "switch", "item", "ball", "flee"))
                act, source = self._auto_action(
                    b, me, enemy, state, steered=policy is not None)
            kind = act[0] if isinstance(act, tuple) and act else act
            if kind == "switch":
                # the switch-in itself eats a hit; Koga got ~10 of them
                state["free_hits"] += 1
            note = source if not disposition else f"{source}/{disposition}"
            state["last_row"] = _turn_row(
                rec, actor="me", action=act, turn=state["turns"],
                enemy_species=(enemy.get("name")
                               if isinstance(enemy, dict) else None),
                enemy_hp_before=(enemy.get("hp")
                                 if isinstance(enemy, dict) else None),
                my_hp_before=me.get("hp") if isinstance(me, dict) else None,
                note=note)
            return act

        # who is actually steering, for anything inspecting the policy
        # Battle.play received (logs, tests, a decider asking "who chose
        # that?"): the wrapper is not the decision-maker, this is.
        wrapped.policy = policy
        wrapped.disposition = disposition
        return state, wrapped

    def _log_turns(self, b, state, outcome):
        """Close the last turn's record and say ONE loud line when the
        battle handed the foe repeated free hits -- the Koga wipe (10 free
        switch-in hits, 5 of 6 mons lost) must be visible at a glance.

        The LOUD number is switch-ins: decide.TurnLog also counts item uses
        and ball throws as ceded turns (they are), but a 4-ball catch is not
        an anomaly and must not cry wolf. Returns
        (switch_ins, ceded_turns)."""
        try:
            self._close_turn(state, b.me(), b.enemy())
        except Exception:
            pass
        free = state["free_hits"]
        ceded = free
        counter = getattr(state.get("log"), "free_hits", None)
        if callable(counter):
            try:
                ceded = counter()
            except Exception:
                ceded = free
        if free > self.FREE_HIT_LOUD:
            log.warning(f"  [fight] free_hits={free} in {state['turns']} "
                        f"turns ({outcome}): every switch-in handed the foe "
                        f"a free hit -- turn record on d.last_battle")
        return free, ceded

    FIGHT_DIAG_CAP = 3   # unresolved-battle dumps per battle (live: 20+)

    def _fight_diag(self, b, outcome):
        """Dump the unresolved battle's screen and both mons' vitals.

        Capped at FIGHT_DIAG_CAP dumps for as long as the SAME battle
        keeps coming back: a caller that retries fight() on a wedged
        battle re-entered this path every time and printed 20+ identical
        dumps per battle in the Victory Road grind. fight() clears the
        counter the moment wBattleMode goes quiet, so the next battle
        starts with a full budget."""
        printed = getattr(self, "_fight_diag_prints", 0)
        if printed >= self.FIGHT_DIAG_CAP:
            return
        self._fight_diag_prints = printed + 1
        try:
            me, enemy = b.me(), b.enemy()
            log.warning(f"  [fight diagnostic] frozen screen ({outcome}):")
            for r in self.emu.screen_text():
                if r.strip():
                    log.info(f"    | {r}")
            mv = [(self.names.moves.get(m, f"?id{m}"), p)
                  for m, p in me["moves"]]
            log.warning(f"  [fight diagnostic] me={me['name']} L{me['level']} "
                  f"{me['hp']}/{me['max_hp']} moves={mv}")
            log.warning(f"  [fight diagnostic] enemy={enemy['name']} "
                  f"L{enemy['level']} {enemy['hp']}/{enemy['max_hp']}",
                  )
        except Exception as diag_err:
            log.warning(f"  [fight diagnostic] unavailable: {diag_err}",
                  )
        if self._fight_diag_prints >= self.FIGHT_DIAG_CAP:
            log.warning(f"  [fight diagnostic] cap reached "
                        f"({self.FIGHT_DIAG_CAP} dumps): suppressing "
                        f"further dumps for this battle")

    def fight(self, max_frames=90000, policy=None, require_decision=False,
              consult_encounter=True, resume=4):
        """Play a battle out with real move selection (best expected
        damage, auto-POTION at low HP, flee hopeless wilds). Pauses at a
        naming keyboard (post-catch nickname prompt) to type
        self._pending_nickname if one is set. `policy=None` falls back
        to self.default_policy (still None by default): scripted battles
        the driver intercepts on its own (talk_to, goto, travel) obey a
        pre-armed policy instead of silently fighting with the default.

        wren pt6 -- the MODEL decides, the harness only reports:
        * a WILD battle asks self.encounter_policy ONCE, before the first
          turn, for a disposition ('ko' | 'catch' | 'flee' |
          ('ball', NAME)): 'catch' throws balls (catch()'s own logic),
          'flee' runs, 'ko' plays the battle out with `policy`. TRAINER
          battles never ask. consult_encounter=False suppresses the
          question for callers that ARE the disposition (catch()).
        * require_decision=True (or self.decide_all) refuses to pick: a
          turn whose policy returns None raises DecisionRequired carrying
          the frame, instead of quietly playing the best-damage move.
        * every turn is recorded on self.last_battle, and a battle with
          more than FREE_HIT_LOUD switch-ins says so in one line.
        * with nothing steering, the harness's pick is logged ('auto:
          attack slot 0 (SURF)') -- a pacing loop once reported fights=0
          while ~20 battles fought themselves.
        * a spent FRAME BUDGET is not a result: `resume` (default 4) more
          budgets are played out before anything is reported unresolved,
          because a long trainer battle just needs more frames (live:
          Lance, five of six down, "UNRESOLVED (timeout)" -- re-calling
          fight() finished it, FUCK_I_MESSED_UP.md #82).
        Policy shapes: policy(rows, me, enemy) (legacy, still supported)
        or policy(frame) -- a single-argument policy is handed the decide
        frame instead. Returns the lead mon, as before."""
        if policy is None:
            policy = self.default_policy
        mode = self.battle()
        if not mode:
            return self.lead()
        must_decide = bool(require_decision) or bool(
            getattr(self, "decide_all", False))
        self._resolve_learn_flow()   # repair a wedged mid-learn state
        moves0 = self._party_moves()   # learn-transparency baseline
        f0 = self.emu.frame
        money0 = game_state(self.emu, self.names)["player"]["money"]
        b = Battle(self.emu, self.names, self.bdata)
        try:
            enemy0 = b.enemy()
        except Exception:
            enemy0 = {}
        disposition = None
        if mode == 1 and consult_encounter:
            # ONE question per wild encounter, asked before any turn
            disposition, policy = self._consult_encounter(b, policy,
                                                          must_decide)
        state, turn_policy = self._turn_policy(b, policy, must_decide,
                                               disposition)
        name = self._resolve_nickname(self._pending_nickname,
                                      b.enemy()["name"])
        outcome = b.play(policy=turn_policy, max_frames=max_frames,
                         want_nickname=bool(name),
                         text_handler=self._battle_text_handler)
        for _ in range(3):                       # naming handoff loop
            if outcome != "naming" or not self.keyboard_open():
                break
            self._pending_nickname = None
            self.dismiss_keyboard(name)
            outcome = b.play(policy=turn_policy, max_frames=max_frames,
                             text_handler=self._battle_text_handler)
        # A spent frame budget is a CLOCK, not an outcome: play() stops
        # after max_frames and re-entering it picks the battle up exactly
        # where it left off. Doing that here is the difference between
        # "Lance took a while" and handing the caller a live battle
        # labelled UNRESOLVED (#82). 'stuck'/'stalled'/'wedged' are NOT
        # resumed -- those mean the battle stopped changing, and more
        # frames buy nothing.
        budgets = 0
        while outcome == "timeout" and self.battle() and budgets < resume:
            budgets += 1
            log.info(f"  [fight] frame budget ({max_frames}) spent with the "
                     f"battle still live -- resuming "
                     f"({budgets}/{resume})")
            outcome = b.play(policy=turn_policy, max_frames=max_frames,
                             text_handler=self._battle_text_handler)
        self._pending_nickname = None
        free_hits, ceded = self._log_turns(b, state, outcome)
        # surface mid-battle level-up swaps (b.play resolved them through
        # _battle_text_handler); the sweep below diffs its own window
        self._diff_learned_moves(moves0)
        self._resolve_learn_flow(4000)   # sweep post-battle leftovers
        self.flush_dialog(3000)
        # Wipe signature: play() reports 'wipe' when the party is down at
        # battle end -- authoritative by itself. The money heuristic below
        # covers wipes whose cutscene resolves during flush_dialog (and
        # the broke-trainer edge where the loss drops Y0), because full HP
        # after the fact proves nothing on its own.
        wiped = outcome == "wipe"
        if not wiped and money0 is not None and not self.battle():
            s = game_state(self.emu, self.names)
            wiped = s["player"].get("money", money0) < money0 and \
                all(m["hp"] == m["max_hp"]
                    for m in s["party"] if not m["egg"])
        if wiped and not self.battle():
            self.whiteouts += 1
            self._whiteout_pending = True
            healed = self._settle_whiteout()
            log.warning(
                f"  [WHITEOUT] wiped -> {self.map_name()} {self.pos()[2:]}; "
                + ("party healed at the Pokécenter" if healed else
                   "party still DOWN -- the whiteout cutscene never "
                   "finished; heal before doing anything else"))
        elif outcome == "wedged":
            # battle.py already printed its own capped wedge diagnostic
            # (frozen screen + vitals fingerprint); don't re-dump the
            # screen here -- the duplicate dump is exactly the hundreds-
            # of-identical-lines spam from wren pt3.
            log.warning(f"  [fight] battle wedged (see battle.py "
                        f"diagnostic above)")
        elif outcome in ("timeout", "stuck", "stalled"):
            # Burn ZERO blind retries: dump the frozen battle so the wedge
            # is diagnosable (the historic Bridget/Jigglypuff freeze cost
            # ~10 retries before anyone looked at the screen).
            self._fight_diag(b, outcome)
        still_live = bool(self.battle())
        if still_live and outcome in ("timeout", "stuck", "stalled",
                                      "wedged"):
            # NEVER report an unresolved fight as if it were over: the
            # caller's next pace()/goto walks straight back into the same
            # live battle (60 'fights', 535s, zero exp on Victory Road).
            log.warning(
                f"  [fight] UNRESOLVED ({outcome}) after {budgets + 1} "
                f"budget(s) of {max_frames}f and the battle is STILL LIVE "
                f"-- calling fight() again RESUMES it from here (that is "
                f"what finished Lance); the next step would re-enter it "
                f"blind instead. Raise max_frames/resume, drive it "
                f"manually, or change the policy")
        if not still_live:
            # battle over: the next one gets a fresh diagnostic budget
            self._fight_diag_prints = 0
        # Scratch sidecar, NOT the working state: a snapshot taken during
        # battle resolution must never become a resumable fork if the leg
        # crashes before the next real save. watch.py can still open
        # <name>.watch.state from its checkpoint browser.
        if self.state_path:
            self.emu.save(Path(self.state_path).with_suffix(".watch.state"))
        lead = self.lead()
        # decision-transparency journal: scripted/auto battles must leave
        # a reviewable trace, or the decider stops making decisions
        # (DESIGN.md rule 1) and persona expression dies in automation
        events = getattr(self, "encounter_events", None)
        if events is not None:   # duck-typed test doubles may omit it
            events.append({
                "frame": f0, "map": self.map_name(),
                "enemy": enemy0.get("name"),
                "enemy_level": enemy0.get("level"),
                "outcome": outcome, "frames": self.emu.frame - f0,
                "moves0": sorted(moves0), "moves1": sorted(
                    self._party_moves()),
                "policy": "custom" if policy is not None else "default",
                "wild": mode == 1, "disposition": disposition,
                "turns": state["turns"], "free_hits": free_hits,
                "ceded_turns": ceded,
                "decided": state["turns"] - state["autos"],
                "battle_live": still_live,
            })
        return lead

    def _settle_whiteout(self, max_frames=12000):
        """Play the whiteout cutscene out, and answer whether the party is
        actually back up.

        Losing warps the player to the last Pokécenter and heals the
        party, but the fade, the warp and the heal take thousands of
        frames and nothing waited for them: fight() logged "auto-healed at
        last Pokécenter" the instant it saw the wipe, while the party was
        still at 0 HP standing on the cell the battle happened on (live:
        AZALEA_TOWN (5,11) with QUILAVA 0/59 and the log claiming a heal).
        A press only pages text, never a choice box (gotcha 13)."""
        f0 = self.emu.frame
        while self.emu.frame - f0 < max_frames:
            rows = self.emu.screen_text()
            if dialog_press_safe(rows):
                self.press("A:2 .:16")
            else:
                self.press(".:20")
            if self.battle():
                continue
            try:
                party = [m for m in game_state(self.emu, self.names)["party"]
                         if not m["egg"]]
            except Exception:
                continue
            if party and all(m["hp"] > 0 for m in party):
                self.settle()
                return True
        return False

    def _whiteout_stop(self, where):
        """Consume a pending wipe flag (set by fight()). Under the default
        'abort' policy, report and tell the caller to stop: continuing the
        plan that just wiped us is how gym legs turned into re-entry
        loops. d.whiteout_policy = 'continue' restores blind resuming."""
        if not self._whiteout_pending:
            return False
        self._whiteout_pending = False
        if self.whiteout_policy == "abort":
            log.warning(f"  [whiteout] aborting {where} -- party healed at "
                  f"{self.map_name()}; relaunch deliberately")
            return True
        return False

    _LEARN_MARKERS = ("TRYING TO LEARN", "WANTS TO LEARN",
                      "DELETE A MOVE", "FORGET A MOVE", "MAKE ROOM",
                      "STOP LEARNING", "FORGOTTEN",
                      # mid-battle _AskForgetMoveText scrolls through a
                      # 2-line box; these cover its middle pages ("But
                      # <MON> can't learn more than four moves." /
                      # "Delete an older move to make room for <MOVE>?")
                      # which used to trip NO marker and dropped the
                      # flow state mid-flow (the GATOR/SCREECH wedge).
                      # Apostrophe-free on purpose (charmap ligatures).
                      "LEARN MORE", "THAN FOUR MOVES", "DELETE AN OLDER")

    HM_MOVES = frozenset(["CUT", "FLY", "SURF", "STRENGTH", "FLASH",
                          "WHIRLPOOL", "WATERFALL"])

    FORGET_PRIORITY = ["SMOKESCREEN", "LEER", "GROWL", "CHARM", "TAIL WHIP",
                       "DEFENSE CURL", "SAND-ATTACK", "TACKLE", "MUD-SLAP",
                       "QUICK ATTACK", "BUBBLE", "EMBER", "SWIFT"]

    learn_moves = True   # accept level-up moves by default

    learn_policy = None

    _learn_flow = None   # per-flow policy state; live only while a flow is

    _learn_source = "auto"

    def _learn_prompt_up(self, rows):
        joined = "".join(rows).upper()
        return any(m in joined for m in self._LEARN_MARKERS)

    def _battle_text_handler(self, rows):
        """Modal-text hook for Battle.play: drive the level-up move-learning
        flow. Returns True when this frame's input was consumed.

        When self.learn_policy is set it is consulted once per flow (see
        the learn_policy attribute for the full contract) at the first
        '<MON> is trying to/wants to learn <MOVE>' page, BEFORE any YES/NO
        is answered: a returned move name answers YES and walks the forget
        menu to THAT move (the cursor row is verified against the request
        with _item_row_matches tolerance before confirming); 'DECLINE'
        answers NO and confirms 'Stop learning'; None, an exception, a
        request not on the menu, or an HM request (game refusal detected)
        all fall back -- with one warning where applicable -- to the AUTO
        policy below.

        AUTO ACCEPT/REPLACE policy (wren pt4, documented from the code --
        this is what actually gets sacrificed):
        * learn_moves=True (default): answer YES to "make room?". On the
          "Which move should be forgotten?" menu, walk the cursor DOWN
          (wrapping) to the FIRST FORGET_PRIORITY move on the list and
          confirm it. When NONE of the mon's moves are in FORGET_PRIORITY,
          the move already under the cursor is confirmed -- the menu opens
          on SLOT 1, so the mon's OLDEST move is what silently disappears
          (how GATOR's BITE became SCARY FACE while a 'press slot 1'
          policy whiffed three Morty fights). HM moves are never
          confirmed: the game refuses, and the cursor is moved off them.
        * learn_moves=False: decline deterministically ("Stop learning
          <MOVE>?" -> YES; B there means "don't stop" and loops).
        Completed swaps (policy- or auto-driven alike) are surfaced by
        _diff_learned_moves (LEARN log line + d.move_changes entry) from
        _resolve_learn_flow / fight().
        Blind A-mashing derails into party menus and wedges the battle."""
        if not self._learn_prompt_up(rows):
            # Transient scroll frames of the mid-battle 2-line box (e.g.
            # "SCREECH." / "But GATOR" while page 2 scrolls in) carry no
            # marker; dropping the flow state there loses a policy
            # DECLINE and the make-room YES/NO then falls through to
            # learn_moves=True (the GATOR/SCREECH forget-menu wedge).
            # Tolerate a few marker-less frames before declaring the
            # flow over.
            st = self._learn_flow
            if st is not None and st.get("misses", 0) < 3:
                st["misses"] = st.get("misses", 0) + 1
            else:
                self._learn_flow = None    # flow over: drop per-flow state
            return False
        joined = "".join(rows).upper()
        st = self._learn_flow
        if st is None:                 # first frame of a fresh flow
            st = self._learn_flow = {"decision": None, "consulted": False,
                                     "answered": False, "mon": None,
                                     "move": None, "misses": 0}
        st["misses"] = 0
        if not st["consulted"] and not st["answered"]:
            self._consult_learn_policy(rows, st)
        decision = st["decision"]
        forget = decision if decision not in (None, "DECLINE") else None
        if "CAN" in joined and "BE FORGOTTEN" in joined:
            # "HM moves can't be forgotten": the refusal text. Acknowledge
            # it; the move menu reopens and the cursor must MOVE off the HM.
            if forget is not None:
                log.warning(f"learn_policy: game refused to forget "
                            f"{forget} (HM) -- falling back to auto")
                st["decision"] = None
                self._learn_source = "auto-fallback"
            self.press("A:4 .:16 D:4 .:16")
            return True
        if "FORGOTTEN" in joined:
            # "Which move should be forgotten?" move menu is up
            if decision == "DECLINE":
                # safety net: a DECLINE flow must never walk this menu
                # (live pt5c wedge: GATOR/SCREECH mid-battle, cursor
                # parked on an HM). B backs out to "Stop learning
                # <MOVE>?", which the YES/NO branch below confirms.
                self.press("B:6 .:20")
                return True
            # A mon whose four moves are ALL HMs (CHAIN: WATERFALL /
            # WHIRLPOOL / STRENGTH / SURF) can never make room: the cursor
            # walk below presses D forever (Victory Road grind, HYDRO PUMP
            # at L40, five 90k-frame budgets). Decline the learn instead.
            table = getattr(getattr(self, "names", None), "moves", None)
            listed = [r.strip().upper().lstrip("▶▷ ") for r in rows
                      if r.strip() and "FORGOTTEN" not in r.upper()]
            named = [r for r in listed
                     if any(mv in r for mv in (table or {}).values())]
            if table and named and all(any(hm in r for hm in self.HM_MOVES)
                                       for r in named):
                log.warning("learn flow: every move on the forget menu is an "
                            "HM -- declining the learn")
                st["decision"] = "DECLINE"
                self.press("B:6 .:20")
                return True
            cur = [r.strip().upper() for r in rows if "▶" in r or "▷" in r]
            on_hm = any(hm in r for r in cur for hm in self.HM_MOVES)
            if forget is not None:
                want = norm_item(forget)
                if forget in self.HM_MOVES:
                    # don't even try: confirming loops through the refusal
                    log.warning(f"learn_policy chose HM move {forget}: the "
                                "game refuses those -- falling back to auto")
                    st["decision"] = forget = None
                    self._learn_source = "auto-fallback"
                elif not any(_item_row_matches(r.lstrip("▶▷ "), want)
                             for r in (x.strip().upper() for x in rows) if r):
                    log.warning(f"learn_policy chose {forget} but it is not "
                                "on the forget menu (stale moveset?) -- "
                                "falling back to auto")
                    st["decision"] = forget = None
                    self._learn_source = "auto-fallback"
            if forget is not None:
                # confirm ONLY once the cursor row itself names the
                # requested move (row-match tolerance); otherwise walk.
                want = norm_item(forget)
                under = any(
                    x >= 0 and _item_row_matches(r[x + 1:], want)
                    for r, x in ((r, max(r.find("▶"), r.find("▷")))
                                 for r in rows))
                self.press("A:6 .:25" if under else "D:4 .:16")
                return True
            target = next((m for m in self.FORGET_PRIORITY if m in joined),
                          None)
            if on_hm:
                self.press("D:4 .:16")     # never confirm an HM move
            elif target is None or any(target in r for r in cur):
                self.press("A:6 .:25")     # forget the move under the cursor
            else:
                self.press("D:4 .:16")     # cursor toward the target (wraps)
            return True
        if "YES" in joined and "NO" in joined:
            st["answered"] = True          # policy window is closed now
            learn = (self.learn_moves if decision is None
                     else decision != "DECLINE")
            if "STOP LEARNING" in joined:
                # decline path confirm; in learn mode B loops back so the
                # make-room prompt can be answered YES this time
                self.press("B:6 .:20" if learn else "A:6 .:20")
            elif learn:
                self.press("A:6 .:25")     # YES: make room for the new move
            else:
                self.press("B:6 .:20")     # NO: keep the current moveset
        else:
            self.press("A:4 .:16")         # advance the flow's text pages
        return True

    def _consult_learn_policy(self, rows, st):
        """Ask self.learn_policy about the learn flow on screen (once per
        flow, before any YES/NO is answered; contract on the attribute).
        Mon and move are parsed off the '<MON> is trying to/wants to learn
        <MOVE>' text and ACCUMULATED on st across frames: the mid-battle
        variant scrolls that sentence through a 2-line box, so mon and
        move are NEVER on screen together there (pt5c: the old
        single-shot regex silently skipped the policy and auto-accepted
        SCREECH). A flow entered MID-WAY (the wedge-repair path) never
        shows either fragment, so the policy is skipped and auto applies.
        A policy that raises is logged once -- exception text plus the
        args it was called with -- and treated as None (auto): a bad
        policy must never wedge a battle."""
        policy = getattr(self, "learn_policy", None) \
            or self.default_learn_policy
        text = re.sub(r"\s+", " ", " ".join(rows)).upper()
        m = re.search(r"(\S+) (?:IS TRYING|WANTS) TO LEARN", text)
        if m:
            st["mon"] = m.group(1)
        m = re.search(r"(?:TRYING|WANTS) TO LEARN "
                      r"([A-Z0-9♂♀'.\- ]+?)[!?.]", text)
        if m:
            st["move"] = m.group(1).strip()
        mon, new_move = st.get("mon"), st.get("move")
        if mon is None or new_move is None:
            return              # sentence still scrolling: retry next frame
        st["consulted"] = True
        moves = next((list(mv) for label, mv in self._party_moves()
                      if label.upper() == mon), [])
        try:
            decision = policy(mon, new_move, moves)
        except Exception as e:
            self._learn_source = "auto-fallback"
            log.warning(f"learn_policy({mon!r}, {new_move!r}, {moves!r}) "
                        f"raised {e!r} -- falling back to auto")
            return
        if decision is not None:
            st["decision"] = str(decision).strip().upper()
            self._learn_source = ("policy" if getattr(self, "learn_policy",
                                                      None) else "default")

    _move_ids = None      # {'IRON TAIL': 231, ...}, lazily inverted

    def move_id(self, name):
        """Move id for a display name, or None. Inverted once from the
        ROM's own MoveNames table."""
        if self._move_ids is None:
            self._move_ids = {norm_item(n): i
                              for i, n in self.names.moves.items()}
        return self._move_ids.get(norm_item(name))

    def move_power(self, name):
        """Base power of a move by display name (0 for status moves and
        for anything the ROM table does not know)."""
        mid = self.move_id(name)
        rec = self.bdata.moves.get(mid) if mid else None
        return (rec or {}).get("power", 0)

    def default_learn_policy(self, mon, new_move, current):
        """The learn decision made when no learn_policy is set: never
        trade damage away for a status move.

        Same contract as learn_policy (a move name to forget, 'DECLINE',
        or None to fall through to AUTO). The old default was
        FORGET_PRIORITY, a hand-ranked NAME list that contains damaging
        moves and, on no match, confirmed slot 1 -- which is how a
        Gyarados traded HYDRO PUMP for RAIN DANCE and GATOR's BITE became
        SCARY FACE. Power comes from the ROM's Moves table, so nothing
        here is a guess about the move list.

        Rules, deterministic on (power, name) so the same flow always
        decides the same way:

        * a status move (power 0) being offered: with two or fewer
          damaging moves left, sacrifice a status move if there is one and
          otherwise DECLINE -- a moveset needs its damage; with three or
          more, still prefer a status move, else the weakest attack.
        * a damaging move being offered: prefer a status move, else the
          weakest attack when it is strictly weaker than the new move,
          else DECLINE (learning something worse is not an upgrade).

        HM moves are never named: the game refuses to delete them and the
        forget menu loops on the refusal.
        """
        try:
            power = {m: self.move_power(m) for m in current}
            new_power = self.move_power(new_move)
        except Exception:
            return None                       # no ROM data: let AUTO run
        forgettable = [m for m in current
                       if m.strip().upper() not in self.HM_MOVES]
        status = sorted((m for m in forgettable if power.get(m, 0) == 0),
                        key=lambda m: (power.get(m, 0), m))
        attacks = sorted((m for m in forgettable if power.get(m, 0) > 0),
                         key=lambda m: (power.get(m, 0), m))
        damaging = [m for m in current if power.get(m, 0) > 0]
        if new_power == 0:
            if status:
                return status[0]
            if len(damaging) <= 2:
                return "DECLINE"
            return attacks[0] if attacks else "DECLINE"
        if status:
            return status[0]
        if attacks and power[attacks[0]] < new_power:
            return attacks[0]
        return "DECLINE"

    def _resolve_learn_flow(self, max_frames=8000):
        """Drive any on-screen move-learning flow to completion. Used to
        repair wedged states and sweep post-battle leftovers; safe to call
        when no flow is present. WHICH move gets sacrificed is decided by
        _battle_text_handler (see its docstring); any completed swap is
        logged and recorded on d.move_changes via _diff_learned_moves."""
        f0 = self.emu.frame
        before = None
        done = True
        while self.emu.frame - f0 < max_frames:
            rows = self.emu.screen_text()
            if not self._learn_prompt_up(rows):
                if before is None:
                    break         # no flow on screen at all
                # mid-scroll transient of the 2-line mid-battle box (no
                # marker while "But <MON>" scrolls in): let the screen
                # settle before declaring the flow over.
                for _ in range(2):
                    self.emu.tick(24)
                    rows = self.emu.screen_text()
                    if self._learn_prompt_up(rows):
                        break
                else:
                    break
            if before is None:       # snapshot only once a flow is real
                before = self._party_moves()
            self._battle_text_handler(rows)
        else:
            done = False
        self._learn_flow = None    # never leak a decision into the next flow
        if before is not None:
            self._diff_learned_moves(before)
        return done

    def _party_moves(self):
        """[(mon label, [move names])] snapshot for learn-flow diffing.
        The label prefers the nickname so LEARN lines match how the party
        is addressed in play (GATOR, REED, ...)."""
        try:
            return [((m.get("nickname") or "").strip() or m.get("name", "?"),
                     [mv["name"] for mv in m.get("moves", [])])
                    for m in game_state(self.emu, self.names)["party"]]
        except Exception:
            return []                 # mid-transition WRAM: skip the diff

    def _diff_learned_moves(self, before):
        """Diff a _party_moves() snapshot against the party NOW: one clear
        LEARN log line per replaced move slot plus an entry on
        d.move_changes ({'mon','forgot','learned','slot','source'},
        slot 1-based; 'source' is 'policy' | 'auto' | 'auto-fallback' --
        who decided the sacrifice, so audits can tell a policy pick from
        a silent fallback: SNAG lost ROCK SLIDE to an exception-swallowed
        policy in pt5c) so policies that press fixed move slots can
        notice their mapping broke (Morty lesson: BITE -> SCARY FACE at
        slot 1 cost three whiteouts). Moves landing in previously EMPTY
        slots shift no existing slot and are not recorded; a mon whose
        label changed (evolution without a nickname, party reorder) is
        skipped rather than misattributed."""
        if not before:
            return []
        after = self._party_moves()
        if not hasattr(self, "move_changes"):
            self.move_changes = []     # bare/duck-typed drivers
        src = getattr(self, "_learn_source", "auto")
        changes = []
        for (b_label, b_mv), (a_label, a_mv) in zip(before, after):
            if b_label != a_label:
                continue
            for i, old in enumerate(b_mv):
                new = a_mv[i] if i < len(a_mv) else None
                if old and new and old != new:
                    changes.append({"mon": a_label, "forgot": old,
                                    "learned": new, "slot": i + 1,
                                    "source": src})
        for c in changes:
            log.warning(f"LEARN: {c['mon']} forgot {c['forgot']} -> "
                        f"learned {c['learned']} (slot {c['slot']})")
        self.move_changes.extend(changes)
        self._learn_source = "auto"    # consumed: next flow starts clean
        return changes

    def _resolve_nickname(self, nickname, species):
        """str passes through; dict is keyed by the wild's species name;
        callable gets the species name. None when nothing applies."""
        if nickname is None:
            return None
        if callable(nickname):
            return nickname(species)
        if isinstance(nickname, dict):
            return nickname.get(species)
        return nickname

    def _ball_policy(self, ball="POKE BALL", max_balls=10):
        """Per-turn policy that throws `ball` until it connects, the ball
        pocket runs dry, or `max_balls` are gone -- then flees rather than
        KO the target. Shared by catch() and the encounter hook's 'catch'
        disposition, so both throw balls exactly the same way."""
        thrown = [0]

        def pol(rows, me, enemy):
            dry = bag_item_index(self.emu, self.names, ball, "balls") is None
            if dry or thrown[0] >= max_balls:
                return "flee"
            thrown[0] += 1
            return ("ball", ball)

        return pol

    def _encounter_ball(self, name=None):
        """Which ball a bare 'catch' disposition throws: the named one, or
        the cheapest ball actually in the pocket -- answering 'catch' must
        not burn an ULTRA BALL on a RATTATA. Falls back to POKE BALL when
        the pocket cannot be read (the ball policy then flees on a dry
        bag rather than KO the target)."""
        if name:
            return name
        for cand in self.BALL_PREFERENCE:
            try:
                if bag_item_index(self.emu, self.names, cand,
                                  "balls") is not None:
                    return cand
            except Exception:
                break
        return self.BALL_PREFERENCE[0]

    def catch(self, ball="POKE BALL", max_balls=10, nickname=None):
        """Throw `ball` at the current wild until it connects or the budget
        runs out; flees rather than KO the target once out of balls.
        `nickname`: str (applied to whatever is caught), dict keyed by
        species name, or callable(species_name) -> str|None.
        This call IS the encounter disposition, so encounter_policy is not
        asked again for this battle."""
        self._pending_nickname = nickname
        try:
            return self.fight(policy=self._ball_policy(ball, max_balls),
                              consult_encounter=False)
        finally:
            self._pending_nickname = None

    def catch_up(self, nickname=None, ball="POKE BALL", max_balls=6,
                 max_encounters=12, label=""):
        """Catch-composition primitive: pace into the nearest grass belt
        on the current map, engage wilds, and throw balls until a catch
        lands or a budget runs out. One tool call instead of ~40 lines of
        bespoke policy per session -- without it deciders price catching
        as high-risk-low-reward and run solo (omp-fresh Q&A #3.1).
        Detection is party-growth based; with a FULL party Crystal routes
        catches to the PC and this cannot see them, so keep a slot open.
        Raises ValueError on a grass-less map and RuntimeError when out
        of balls mid-hunt. Returns a structured outcome dict."""
        import random
        if label:
            log.info(f"[{label}] catch_up on {self.map_name()}")
        grass = self._grass_cells()
        if not grass:
            raise ValueError(f"no grass on {self.map_name()} -- travel to "
                             "a route with grass first")

        def _balls():
            return self._bag().get(norm_item(ball), 0)

        if _balls() == 0:
            raise RuntimeError(f"catch_up: no {ball} in the bag")
        known = {m["name"] for m in game_state(self.emu, self.names)["party"]}
        stall_cycles = 0
        encounters = used_total = 0
        while encounters < max_encounters:
            if self.battle():
                encounters += 1
                b0 = _balls()
                self.catch(nickname=nickname, ball=ball, max_balls=max_balls)
                used_total += max(0, b0 - _balls())
                gs = game_state(self.emu, self.names)["party"]
                fresh = [m for m in gs if m["name"] not in known]
                if fresh:
                    m = fresh[-1]
                    log.info(f"  catch_up: caught {m['name']} "
                          f"({used_total} balls, {encounters} encounters)")
                    return {"caught": True, "species": m["name"],
                            "nick": m.get("nickname"),
                            "level": m["level"], "balls_used": used_total,
                            "encounters": encounters,
                            "party_size": len(gs)}
                if self._bag().get(norm_item(ball), 0) == 0:
                    raise RuntimeError(
                        f"catch_up: out of {ball} after {encounters} "
                        f"encounters, {used_total} thrown -- restock")
                stall_cycles = 0
                continue
            # no battle this cycle: scene-sealed grass (R29 tutorial)
            # or unreachable belt -- a plain retry loops FOREVER here
            # (moss-run: ~4600 cycles until eval timeout killed the
            # kernel), so count and raise with the goto diagnosis.
            stall_cycles += 1
            if stall_cycles >= 4:
                raise RuntimeError(
                    f"catch_up: {stall_cycles} pace cycles, zero "
                    f"encounters on {self.map_name()} -- grass sealed "
                    f"or unreachable? last_goto_reason="
                    f"{self.last_goto_reason!r} last_choice_options="
                    f"{self.last_choice_options} (resolve_choice the "
                    f"box / d.trip_scenes the cell, then retry)")
            obs = self.observe()
            cx, cy = obs["x"], obs["y"]
            near = sorted(grass, key=lambda c: abs(c[0] - cx)
                          + abs(c[1] - cy))[:8]
            for tx, ty in near:
                try:
                    saved_af, self.auto_fight = self.auto_fight, True
                    try:
                        self.goto(tx, ty, "into the grass")
                    finally:
                        self.auto_fight = saved_af
                    break
                except Exception:
                    continue
            else:
                raise RuntimeError("catch_up: no reachable grass cell on "
                                   f"{self.map_name()}")
            steps = 0
            while not self.battle() and steps < 60:
                o = self.observe()
                tiles, npcs = o["tiles"], {tuple(c) for c in o["npcs"]}
                px, py = o["x"], o["y"]
                opts = []
                for dd, kind in tiles.items():
                    if dd == "here":
                        continue
                    mv = dd.upper()
                    dx, dy = STEP[mv]
                    if (px + dx, py + dy) in npcs:
                        continue
                    if kind == "grass":
                        opts += [mv] * 3      # bias toward re-entering
                    elif kind == "floor":
                        opts.append(mv)
                if not opts:
                    break                     # boxed in; outer relocates
                res = self.step_dir(random.choice(opts))
                if res == "battle":
                    break
                steps += 1
        return {"caught": False, "species": None, "nick": None,
                "balls_used": used_total, "encounters": encounters}

    _HEAL_CENTERS = ("CHERRYGROVE_POKECENTER_1F", "VIOLET_POKECENTER_1F",
                     "ROUTE_32_POKECENTER_1F", "AZALEA_POKECENTER_1F",
                     "GOLDENROD_POKECENTER_1F", "ECRUTEAK_POKECENTER_1F")

    def _grass_cells(self):
        """All tall/long-grass collision cells on the current map."""
        grid = self.nav.grid(self.map_name())
        return [(x, y) for y in range(len(grid))
                for x in range(len(grid[y])) if grid[y][x] in (0x14, 0x18)]

    def _train_heal(self):
        """Mid-training nurse trip: route to whichever Pokécenter actually
        routes shortest, heal, route back. Raises when nothing routes --
        silent 'kept training hurt' would be worse. Transit forces
        auto_fight: manual mode means the DECIDER owns battles at the
        train()/catch_up() call level, not every wild on the nurse rail
        (moss-run [W]: sticky flag starved the heal rail mid-leg)."""
        saved = self.auto_fight
        self.auto_fight = True
        try:
            self._train_heal_inner()
        finally:
            self.auto_fight = saved

    def _train_heal_inner(self):
        here = self.map_name()
        best, best_len = None, None
        for cand in self._HEAL_CENTERS:
            try:
                plan = self.route(cand)
            except Exception:
                continue
            if plan and (best_len is None or len(plan) < best_len):
                best, best_len = cand, len(plan)
        if best is None:
            raise RuntimeError(f"train: no routable Pokécenter from {here};"
                               " heal manually or move nearer a town")
        if best != here:
            self.travel(best)
            if "POKECENTER" not in self.map_name():
                raise RuntimeError(f"train: travel to {best} landed on "
                                   f"{self.map_name()}")
        heal_pokecenter(self)
        if best != here:
            self.travel(here)
        log.info("  train: nurse heal done")

    def train(self, target_level, max_battles=150, targets=None):
        """Rotation-train every non-egg party member to >= target_level in
        the nearest grass patch on the current map; returns the min party
        level. Caller must stand on a map WITH grass (ValueError otherwise)
        -- explicit failure beats silently wandering in search of one.
        `targets`: {nickname-or-species: level} per-mon goals; a mon at or
        above ITS goal stops counting toward done, so a carry can't mask a
        starving teammate (moss-run [W]: BRAMBLE sat L2 through 160
        battles while the carry ate the budget). Mon not named uses
        target_level. Biggest gap rotates in first.
        Level-up learns are accepted per _battle_text_handler's policy;
        any REPLACED move is logged (LEARN: ...) and appended to
        d.move_changes -- check it before reusing slot-based policies."""
        import random
        targets = {k.upper(): v for k, v in (targets or {}).items()}

        def _goal(m):
            return targets.get((m.get("nick") or "").upper(),
                               targets.get(m["species"].upper(),
                                           target_level))

        grass = self._grass_cells()
        if not grass:
            raise ValueError(f"no grass on {self.map_name()} -- walk/travel "
                             "to a route with grass first")
        log.info(f"[train] target L{target_level}, cap {max_battles} battles"
                 f"{', per-mon ' + str(targets) if targets else ''}",)
        battles = dry = 0
        changes0 = len(self.move_changes)
        while True:
            obs = self.observe()
            party = obs["party"]
            members = [(i, m) for i, m in enumerate(party)
                       if not m.get("egg")]
            underleveled = any(m["level"] < _goal(m) for _, m in members)
            if not underleveled or battles >= max_battles:
                break
            lead = party[0]
            sick = any(m.get("status") == "PSN" or m["hp"] <= 0
                       for _, m in members)
            if sick or lead["hp"] / max(lead["max_hp"], 1) < 0.35:
                # The rail is only worth walking if healing can actually
                # change the party. An already-full party that still looks
                # "sick" means something the nurse cannot fix (an egg read
                # as fainted, a permanent status): bail loudly instead of
                # round-tripping to the Pokécenter forever -- that loop ate
                # 30+ trips and zero battles (FUCK_I_MESSED_UP.md #20).
                if all(m["hp"] >= m["max_hp"] and not m.get("status")
                       for _, m in members):
                    raise RuntimeError(
                        "train: heal rail asked for while every non-egg "
                        "member is already full -- refusing to loop; check "
                        "for an egg or an unhealable status in the party")
                log.info(f"  train: healing rail ({lead['species']} "
                      f"{lead['hp']}/{lead['max_hp']})")
                self._train_heal()
                continue               # relocate grass from wherever we land
            # A mon with no damaging move cannot land a KO, so it earns no
            # exp no matter how many encounters it sees: rotating it in
            # burned 60 battles for zero levels. Keep it out of the
            # rotation and say so.
            ids = {n: i for i, n in self.names.moves.items()}

            def _can_damage(m):
                for mv in m["moves"]:
                    row = self.bdata.moves.get(ids.get(mv["name"], -1)) or {}
                    if row.get("power"):
                        return True
                return False
            blocked = [m["nick"] for _, m in members
                       if m["hp"] > 0 and m["level"] < _goal(m)
                       and not _can_damage(m)]
            elig = sorted((i for i, m in members
                           if m["hp"] > 0 and m["level"] < _goal(m)
                           and _can_damage(m)),
                          key=lambda i: _goal(party[i]) - party[i]["level"],
                          reverse=True)   # biggest gap rotates in first
            if not elig and blocked:
                raise RuntimeError(
                    "train: the only under-levelled members have no "
                    f"damaging move ({', '.join(blocked)}) -- they cannot "
                    "KO anything and will never gain exp; teach a damaging "
                    "move or raise them another way")
            if not elig:
                # everyone still under target is FAINTED: the rail above
                # only fires on lead-HP/poison, so revive explicitly
                # instead of reporting a bogus 'done' (bit a verify run:
                # Poliwag fainted -> elig empty -> exited at min L4/10).
                self._train_heal()
                continue
            if not self.battle():
                cx, cy = obs["x"], obs["y"]
                near = sorted(grass, key=lambda c: abs(c[0] - cx)
                              + abs(c[1] - cy))[:8]
                for tx, ty in near:
                    try:
                        saved_af, self.auto_fight = self.auto_fight, True
                        try:
                            self.goto(tx, ty, "into the grass")
                        finally:
                            self.auto_fight = saved_af
                        break
                    except Exception:
                        continue
                else:
                    raise RuntimeError("train: no reachable grass cell on "
                                       f"{self.map_name()}")
                while not self.battle():
                    if self.menu_open():
                        # a leftover post-battle modal silently eats every
                        # movement press (gotcha 7) -- 400 'dry steps' of
                        # nothing. B out of it before pacing on.
                        self.close_menus()
                        continue
                    o = self.observe()
                    tiles = o["tiles"]
                    npcs = {tuple(c) for c in o["npcs"]}
                    px, py = o["x"], o["y"]
                    opts = []
                    for dd, kind in tiles.items():
                        if dd == "here":
                            continue
                        mv = dd.upper()
                        dx, dy = STEP[mv]
                        if (px + dx, py + dy) in npcs:
                            continue
                        if kind == "grass":
                            opts += [mv] * 3      # bias toward re-entering
                        elif kind == "floor":
                            opts.append(mv)
                    if not opts:
                        break            # boxed in; outer loop relocates
                    res = self.step_dir(random.choice(opts))
                    if res == "battle":
                        break
                    dry += 1
                    if dry > 400:
                        raise RuntimeError(
                            "train: 400 steps, zero encounters -- grid "
                            "says grass but terrain disagrees?")
            nxt = elig[battles % len(elig)]
            tgt = party[nxt]
            switched = [False]

            def policy(rows, me, enemy, _nxt=nxt, _tgt=tgt,
                       _did=switched):
                """Once per battle: rotate the next underleveled member in;
                afterwards None lets the default smart attack policy take
                over. A hurting active mon FLEES instead of falling through
                to the default potion flow: its target-slot-0 heal lands on
                a full-HP lead ("no effect"), never consumes, and the
                potion target list wedged 150 battles straight. The nurse
                rail between battles does the healing instead."""
                if not _did[0] and _nxt and not (
                        me["name"] == _tgt["species"]
                        and me["level"] == _tgt["level"]
                        and me["max_hp"] == _tgt["max_hp"]):
                    _did[0] = True
                    return ("switch", _nxt)
                if me["hp"] / max(me["max_hp"], 1) < 0.30:
                    return "flee"       # trainer battles: fails, wedge
                return None             # guard degrades to plain attack

            self.fight(policy=policy)
            battles += 1
            dry = 0
            snap = [(m["species"], m["level"], m["hp"], m["max_hp"])
                    for _, m in members]
            log.info(f"  train: battle {battles}/{max_battles} {snap}")
            if battles % 10 == 0:
                self.save()
        final = [m["level"] for m in self.observe()["party"]
                 if not m.get("egg")]
        lo = min(final) if final else 0
        log.info(f"[train] done after {battles} battles: party min L{lo}"
              f"{' (target reached)' if lo >= target_level else ''}",
              )
        swapped = self.move_changes[changes0:]
        if swapped:
            log.warning(f"[train] {len(swapped)} move slot(s) changed by "
                        "level-up learns this run (LEARN lines above; "
                        "d.move_changes has details) -- re-check any "
                        "policy that presses fixed move slots")
        self.save()
        return lo
