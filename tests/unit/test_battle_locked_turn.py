"""claude-wren pt7 Victory Road wedge: the foe (ONIX) had BOUND the active
mon, the policy asked for ('switch', idx), the harness drove the party menu
and confirmed SWITCH, and the ENGINE REFUSED it -- "<MON> can't be recalled!".
Nothing handled the refusal, so the party menu stayed open with the submenu
cursor parked on SWITCH, the screen stopped changing, fight() spun on 'frozen
screen' and returned 'timeout' with the battle STILL LIVE; the next pace()
walked back into it. 60 "fights", 535s, zero exp.

Ground truth, all from the disassembly (nothing here is guessed frame counts):

  engine/battle/core.asm  BattleMenuPKMN_Loop
      the battle PKMN entry stacks TWO menus: the scrolling party list
      (SelectBattleMon) and BattleMonMenu drawn over it, whose pick the loop
      reads back as `ld a, [wMenuCursorY] / cp $1 SWITCH / cp $2 STATS /
      cp $3 CANCEL`.
  engine/pokemon/mon_submenu.asm  BattleMonMenu (:247)
      `menu_coords 11, 11, SCREEN_WIDTH - 1, SCREEN_HEIGHT - 1`, three fixed
      items "SWITCH@" / "STATS@" / "CANCEL@", default option 1 -- so the box
      lives at column 11 and both menus keep a cursor glyph painted at once
      (AGENTS.md gotcha 1: U+25B7 party list, U+25B6 submenu).
  engine/battle/core.asm  TryPlayerSwitch .check_trapped
      `ld a, [wPlayerWrapCount] / and a / jr nz, .trapped` and
      `ld a, [wEnemySubStatus5] / bit SUBSTATUS_CANT_RUN, a` -> prints
      BattleText_MonCantBeRecalled and `jp BattleMenuPKMN_Loop`, i.e. back
      into the OPEN party list with the switch un-done.
  engine/battle/core.asm  BattleTurn
      `call CheckPlayerLockedIn / jr c, .skip_iteration` skips the whole
      FIGHT/PKMN/PACK/RUN menu on a forced turn (wPlayerSubStatus4
      SUBSTATUS_RECHARGE, wPlayerSubStatus3 SUBSTATUS_CHARGED|SUBSTATUS_
      RAMPAGE, wPlayerSubStatus1 SUBSTATUS_ROLLOUT); ParsePlayerAction
      additionally skips MoveSelectionScreen while Encored.
"""
import logging
from types import SimpleNamespace

import pytest

import trek
from trek import Driver
from crystalagent.battle import Battle
from crystalagent.menus import Menus, _cursor_x, _cursor_xs

pytestmark = pytest.mark.unit

# SUBSTATUS_* bit numbers: constants/battle_constants.asm
ROLLOUT = 1 << 6          # wPlayerSubStatus1
RAMPAGE = 1 << 1          # wPlayerSubStatus3
CHARGED = 1 << 4          # wPlayerSubStatus3
RECHARGE = 1 << 5         # wPlayerSubStatus4
ENCORED = 1 << 4          # wPlayerSubStatus5
CANT_RUN = 1 << 7         # wEnemySubStatus5


class FakeEmu:
    """Just enough Crystal to be DRIVEN: the action menu, the scrolling
    party list, BattleMonMenu painted over it with BOTH cursor glyphs live,
    the move list, the "can't be recalled!" prompt, and forced turns that
    draw no menu at all. Every menu is positioned through the same
    wMenuCursorY / wMenuCursorX / wMenuScrollPosition cells the engine uses.
    """

    PARTY = [("BROOK", 148, 148, 48), ("GATOR", 285, 285, 76),
             ("SNAG", 125, 125, 43), ("RIPTIDE", 40, 146, 44),
             ("PEBBLE", 111, 111, 39), ("REED", 60, 60, 30)]
    MOVES = ["WATERFALL", "WHIRLPOOL", "DRAGON RAGE", "HYDRO PUMP"]
    SUBMENU = ("SWITCH", "STATS", "CANCEL")

    def __init__(self, wrap=0, active=3, party_cursor=5, hide_trap=False,
                 inert=False, enemy_hp=65):
        self.frame = 0
        self.state = "menu"
        self.active = active
        self.enemy_hp = enemy_hp
        self.enemy_max = enemy_hp
        self.party = [list(p) for p in self.PARTY]
        self.wram = {"wBattleMode": 1, "wPlayerWrapCount": wrap,
                     "wPlayerSubStatus1": 0, "wPlayerSubStatus3": 0,
                     "wPlayerSubStatus4": 0, "wPlayerSubStatus5": 0,
                     "wEnemySubStatus5": 0}
        self.cy, self.cx, self.scroll = 1, 1, 0
        self.party_cursor = party_cursor    # 1-based row the ▷ sits on
        self.sel = active
        self.hide_trap = hide_trap          # symbol table can't answer
        self.inert = inert                  # menus respond, nothing happens
        self.blink = False
        self.acts = []                      # what the ENGINE actually did
        self.lock_label = None
        self.lock_bit = 0
        self.lock_left = 0
        self.lock_name = ""
        self.encore_left = 0

    # -- scripted engine states ---------------------------------------------

    def arm_lock(self, label, bit, turns, name="forced"):
        """Put the player in a forced turn the way CheckPlayerLockedIn sees
        it: the bit is set and the battle menu is not drawn until it
        clears."""
        self.wram[label] = self.wram[label] | bit
        self.lock_label, self.lock_bit = label, bit
        self.lock_left, self.lock_name = turns, name
        self.state = "locked"

    def arm_encore(self, turns):
        """Encore: ParsePlayerAction skips MoveSelectionScreen, so the
        action menu IS drawn but FIGHT resolves straight into last move."""
        self.wram["wPlayerSubStatus5"] |= ENCORED
        self.encore_left = turns

    # -- WRAM ----------------------------------------------------------------

    def read_u8(self, label):
        if label == "wMenuCursorY":
            return self.cy
        if label == "wMenuCursorX":
            return self.cx
        if label == "wMenuScrollPosition":
            return self.scroll
        if label == "wPlayerWrapCount" and self.hide_trap:
            raise KeyError(label)       # symbol table cannot answer
        return self.wram[label]

    # -- screen ---------------------------------------------------------------

    def _party_rows(self):
        rows = []
        for i, (name, hp, mx, lv) in enumerate(self.party):
            mark = "▷" if i + 1 == self.party_cursor else " "
            rows.append(f"{mark}{name:<8}{hp:>4}/{mx:<4}L{lv}")
        mark = "▷" if self.party_cursor == len(self.party) + 1 else " "
        rows.append(f"{mark}CANCEL")
        rows.append("Choose a POKéMON.")
        return rows

    def _with_submenu(self, rows):
        """BattleMonMenu over the party list at column 11 -- the party
        list's own ▷ stays painted underneath, so one row carries BOTH
        glyphs (exactly the live wedge screen)."""
        out = list(rows)
        top = len(self.party) - 2       # lands the box on the ▷ row
        for j, label in enumerate(self.SUBMENU):
            y = top + j
            base = out[y].ljust(11)[:11] if 0 <= y < len(out) else " " * 11
            mark = "▶" if self.cy == j + 1 else " "
            out[y] = f"{base}│{mark}{label:<6}│"
        return out

    def _menu_rows(self):
        rows = ["ONIX          L32", f"  {self.enemy_hp}/{self.enemy_max}",
                "", "", "FIGHT   PKMN", "PACK    RUN"]
        if self.inert and self.blink:
            rows.append("▼")            # the live wedge repainted, too
        return rows

    def screen_text(self):
        if self.state == "menu":
            return self._menu_rows()
        if self.state == "party":
            return self._party_rows()
        if self.state == "submenu":
            return self._with_submenu(self._party_rows())
        if self.state == "refusal":
            return ["", "", "", "", "┌──────────────────┐",
                    "│RIPTIDE           │",
                    "│can't be recalled!│",
                    "└─────────────────▼┘"]
        if self.state == "locked":
            return ["ONIX          L32", "", "",
                    f"RIPTIDE is {self.lock_name}!"]
        if self.state == "moves":
            return [f"{'▶' if self.cy == i + 1 else ' '}{m}"
                    for i, m in enumerate(self.MOVES)]
        raise AssertionError(self.state)

    # -- input ----------------------------------------------------------------

    def run_sequence(self, steps):
        start = self.frame
        for buttons, frames in steps:
            self.frame += frames
            if buttons:
                self._press(next(iter(buttons)))
                self.frame += 2
        return self.frame - start

    def _press(self, b):
        getattr(self, "_press_" + self.state)(b)

    def _press_menu(self, b):
        if b == "down":
            self.cy = min(self.cy + 1, 2)
        elif b == "up":
            self.cy = max(self.cy - 1, 1)
        elif b == "right":
            self.cx = min(self.cx + 1, 2)
        elif b == "left":
            self.cx = max(self.cx - 1, 1)
        elif b == "a":
            if (self.cy, self.cx) == (1, 1):        # FIGHT
                if self.wram["wPlayerSubStatus5"] & ENCORED:
                    self.acts.append(("encore-move", None))
                    self._resolve_attack()
                else:
                    self.state, self.cy = "moves", 1
            elif (self.cy, self.cx) == (1, 2):      # PKMN
                self.state, self.scroll = "party", 0
                self.cy = self.party_cursor
            elif (self.cy, self.cx) == (2, 2):      # RUN
                self.acts.append(("run", None))
                if self.inert or self.wram["wPlayerWrapCount"]:
                    # TryToRunAwayFromBattle .cant_escape falls through to
                    # `jp BattleMenu`: the turn is NOT consumed, the menu
                    # simply comes back
                    self.blink = not self.blink
                else:
                    self.wram["wBattleMode"] = 0

    def _press_party(self, b):
        rows = len(self.party) + 1                  # + CANCEL
        if b == "down":
            self.cy = min(self.cy + 1, rows)
        elif b == "up":
            self.cy = max(self.cy - 1, 1)
        elif b == "a":
            self.party_cursor = self.cy
            self.sel = self.cy - 1
            self.state, self.cy = "submenu", 1      # .MenuHeader default 1
        elif b == "b":
            self.state, self.cy, self.cx = "menu", 1, 1

    def _press_submenu(self, b):
        if b == "down":
            self.cy = min(self.cy + 1, 3)
        elif b == "up":
            self.cy = max(self.cy - 1, 1)
        elif b == "b":
            self.state, self.cy = "party", self.party_cursor
        elif b == "a":
            if self.cy == 1:                        # TryPlayerSwitch
                if self.wram["wPlayerWrapCount"] or \
                        self.wram["wEnemySubStatus5"] & CANT_RUN:
                    self.acts.append(("switch-refused", self.sel))
                    self.state = "refusal"
                else:
                    self.acts.append(("switch", self.sel))
                    self.active = self.sel
                    self.state, self.cy, self.cx = "menu", 1, 1
                    self._enemy_turn()
            elif self.cy == 3:                      # CANCEL
                self.state, self.cy, self.cx = "menu", 1, 1

    def _press_refusal(self, b):
        # BattleText_MonCantBeRecalled is a `prompt` box and the engine
        # jumps back to BattleMenuPKMN_Loop: the PARTY list, still open.
        if b in ("a", "b"):
            self.state, self.cy = "party", self.party_cursor

    def _press_locked(self, b):
        self.acts.append(("forced", self.lock_name))
        self.lock_left -= 1
        self._enemy_turn()
        if self.lock_left <= 0:
            self.wram[self.lock_label] &= ~self.lock_bit
            self.state, self.cy, self.cx = "menu", 1, 1

    def _press_moves(self, b):
        if b == "down":
            self.cy = self.cy % 4 + 1
        elif b == "up":
            self.cy = (self.cy - 2) % 4 + 1
        elif b == "b":
            self.state, self.cy, self.cx = "menu", 1, 1
        elif b == "a":
            self.acts.append(("attack", self.cy - 1))
            self._resolve_attack()

    # -- turn resolution -------------------------------------------------------

    def _resolve_attack(self):
        self.state, self.cy, self.cx = "menu", 1, 1
        if self.encore_left:
            self.encore_left -= 1
            if not self.encore_left:
                self.wram["wPlayerSubStatus5"] &= ~ENCORED
        if self.inert:
            self.blink = not self.blink     # repaints, changes nothing
            return
        self.enemy_hp = max(self.enemy_hp - 25, 0)
        if self.enemy_hp:
            self._enemy_turn()
        else:
            self.wram["wBattleMode"] = 0

    def _enemy_turn(self):
        wrap = self.wram["wPlayerWrapCount"]
        if wrap:
            self.wram["wPlayerWrapCount"] = wrap - 1
        mon = self.party[self.active]
        mon[1] = max(mon[1] - 5, 1)


class Harness(Battle):
    """Battle wired to FakeEmu. Every menu primitive, switch_to, the
    forced-turn handling and play() run FOR REAL; only the mon-struct
    readers are stubbed."""

    def __init__(self, emu, default_act="attack"):
        self.emu = emu
        self.names = SimpleNamespace(
            moves={i: n for i, n in enumerate(FakeEmu.MOVES, start=1)},
            species={})
        self.menu = Menus(emu)
        self.switch_refused = False
        self.default_act = default_act

    def active(self):
        return self.emu.wram["wBattleMode"] != 0

    def me(self):
        name, hp, mx, lv = self.emu.party[self.emu.active]
        return {"species": 130 + self.emu.active, "name": name,
                "nickname": name, "party_slot": self.emu.active,
                "level": lv, "hp": hp, "max_hp": mx, "types": [11],
                "status": [], "moves": [(i, 10) for i in range(1, 5)]}

    def enemy(self):
        return {"species": 95, "name": "ONIX", "nickname": "ONIX",
                "party_slot": 0, "level": 32, "hp": self.emu.enemy_hp,
                "max_hp": self.emu.enemy_max, "types": [5], "status": []}

    def best_move(self):
        return 0

    def party_alive(self):
        return any(p[1] > 0 for p in self.emu.party)

    def _party_count(self):
        return len(self.emu.party)

    def _alive_slots(self):
        return [i for i, p in enumerate(self.emu.party) if p[1] > 0]

    def _egg_slots(self):
        return set()

    def bag_item_index(self, name, pocket="items"):
        return None

    def _default_policy(self, me, enemy, potion_frac):
        return self.default_act


def warnings_of(caplog):
    return [r.getMessage() for r in caplog.records]


# -- (1) the nested two-glyph menu --------------------------------------------

def test_submenu_is_read_past_the_party_lists_own_cursor():
    """The live wedge screen: the party list keeps ▷ painted while
    BattleMonMenu paints ▶ on the SAME row. A leftmost-glyph read (the old
    Menus.has_label) sees "PEBBLE ...│▶SWITCH│" and never finds SWITCH."""
    emu = FakeEmu()
    emu.state, emu.cy, emu.sel = "submenu", 1, 4
    emu.party_cursor = len(emu.party) - 1
    h = Harness(emu)
    rows = emu.screen_text()
    both = [r for r in rows if "▷" in r and "▶" in r]
    assert both, rows
    assert _cursor_x(both[0]) == 0                  # leftmost = the party list
    assert len(_cursor_xs(both[0])) == 2            # ... but there are two
    assert h._submenu_choice(rows) == "SWITCH"
    assert h._submenu_up(rows) is True
    emu.cy = 2
    assert h._submenu_choice(emu.screen_text()) == "STATS"


def test_action_menu_alone_is_not_a_submenu():
    emu = FakeEmu()
    h = Harness(emu)
    assert h._submenu_choice(emu.screen_text()) is None
    emu.state, emu.cy = "party", 5
    assert h._submenu_choice(emu.screen_text()) is None


# -- (2) a legal switch drives the whole nested sequence ----------------------

def test_switch_drives_party_list_and_submenu_to_completion():
    """PKMN -> party row -> A -> SWITCH -> A. The party cursor starts BELOW
    the target (the list remembers it between opens), so the walk has to
    climb: Menus.select_abs only ever presses DOWN."""
    emu = FakeEmu(party_cursor=5)
    h = Harness(emu)
    assert h.switch_to(1) is True
    assert emu.acts == [("switch", 1)]
    assert emu.active == 1
    assert emu.state == "menu"          # never leaves a menu open
    assert h.switch_refused is False


# -- (3) the engine refuses the switch ----------------------------------------

def test_refused_switch_is_dismissed_backed_out_and_latched(caplog):
    """TryPlayerSwitch .trapped -> BattleText_MonCantBeRecalled ->
    `jp BattleMenuPKMN_Loop`. The harness must clear the prompt, leave the
    party menu, report the misfire and drop switching from the legal set."""
    emu = FakeEmu(wrap=3)
    h = Harness(emu)
    with caplog.at_level(logging.WARNING, logger="trek"):
        assert h.switch_to(1) is False
    assert emu.acts == [("switch-refused", 1)]
    assert emu.active == 3                  # the switch never happened
    assert emu.state == "menu"              # NOT parked on the open submenu
    assert h.switch_refused is True
    assert h.switch_blocked_reason() is not None
    assert any("can't be recalled" in m for m in warnings_of(caplog))


def test_switch_blocked_while_wrapped_and_released_when_it_expires():
    emu = FakeEmu(wrap=2)
    h = Harness(emu)
    assert h.trapped() is True
    assert "recalled" in h.switch_blocked_reason()
    emu.wram["wPlayerWrapCount"] = 0
    assert h.trapped() is False
    assert h.switch_blocked_reason() is None


def test_mean_look_traps_without_a_wrap_counter():
    emu = FakeEmu()
    emu.wram["wEnemySubStatus5"] = CANT_RUN
    h = Harness(emu)
    assert h.trapped() is True
    assert h.switch_blocked_reason() is not None
    assert h._flee_blocked_reason() is not None


def test_latch_survives_an_unreadable_symbol_table():
    """With wPlayerWrapCount unreadable trapped() is UNKNOWN (None), so the
    observed refusal is the only evidence -- the latch must hold."""
    emu = FakeEmu(wrap=3, hide_trap=True)
    h = Harness(emu)
    assert h.trapped() is None
    assert h.switch_blocked_reason() is None
    h.switch_refused = True
    assert "refused the last switch" in h.switch_blocked_reason()


# -- (4) the whole battle: trapped switch policy still wins -------------------

def test_trapped_switch_policy_never_even_tries_and_wins(caplog):
    """wPlayerWrapCount is readable, so ('switch', 1) is rejected before a
    single menu press: substituted with an attack, battle won."""
    emu = FakeEmu(wrap=3)
    h = Harness(emu)
    with caplog.at_level(logging.WARNING, logger="trek"):
        outcome = h.play(policy=lambda rows, me, en: ("switch", 1))
    assert outcome == "won"
    kinds = [k for k, _ in emu.acts]
    assert "switch-refused" not in kinds and "switch" not in kinds
    assert kinds.count("attack") == 3           # 65 hp / 25 per hit
    msgs = warnings_of(caplog)
    assert any("can't be recalled" in m and "impossible" in m for m in msgs)
    assert not any("frozen screen" in m for m in msgs)


def test_screen_only_refusal_is_absorbed_and_the_battle_is_won(caplog):
    """The acceptance case: the engine's refusal textbox is the ONLY
    evidence. One switch is attempted, refused, dismissed and latched;
    every later turn attacks; the battle resolves with no frozen-screen
    strike and no timeout."""
    emu = FakeEmu(wrap=9, hide_trap=True)
    h = Harness(emu)
    with caplog.at_level(logging.WARNING, logger="trek"):
        outcome = h.play(policy=lambda rows, me, en: ("switch", 1))
    assert outcome == "won"
    kinds = [k for k, _ in emu.acts]
    assert kinds.count("switch-refused") == 1   # tried once, never again
    assert kinds.count("attack") == 3
    assert emu.state == "menu"
    assert h.switch_refused is True
    msgs = warnings_of(caplog)
    assert any("switch refused" in m for m in msgs)
    assert not any("frozen screen" in m for m in msgs)


def test_frame_hides_switch_targets_while_trapped(monkeypatch):
    """battle_frame promises can_switch lists LEGAL targets; a trapped mon
    has none, so a policy cannot even propose the illegal action. It was
    lying here: the frame kept offering slots the engine would refuse."""
    from crystalagent import decide

    party = [{"species": n, "nickname": n, "level": 40, "hp": 10,
              "max_hp": 10, "egg": False, "moves": [], "status": []}
             for n in ("BROOK", "GATOR", "SNAG", "RIPTIDE")]
    emu = FakeEmu(wrap=3)
    h = Harness(emu)
    h.data = SimpleNamespace(moves={}, effectiveness=lambda a, b: 1.0)
    monkeypatch.setattr(decide, "read_bag", lambda *a, **k: {})

    def frame():
        return decide.battle_frame(h, h.names, h.data, party=party,
                                   battle=h)

    assert frame()["can_switch"] == []
    emu.wram["wPlayerWrapCount"] = 0        # the trap runs out
    assert frame()["can_switch"] == [0, 1, 2]


# -- (5) forced turns: wait and re-poll, never re-send ------------------------

@pytest.mark.parametrize("label,bit,name", [
    ("wPlayerSubStatus4", RECHARGE, "recharging"),
    ("wPlayerSubStatus3", RAMPAGE, "rampaging"),
    ("wPlayerSubStatus3", CHARGED, "charging"),
    ("wPlayerSubStatus1", ROLLOUT, "rollout"),
])
def test_forced_turns_are_waited_out_then_control_returns(label, bit, name,
                                                          caplog):
    """CheckPlayerLockedIn draws NO menu for three turns: the harness ticks
    frames, never sends an action, never takes a frozen-screen strike, and
    the policy gets the wheel back the turn the lock expires."""
    emu = FakeEmu()
    emu.arm_lock(label, bit, 3, name)
    h = Harness(emu)
    asked = []

    def policy(rows, me, enemy):
        asked.append(h.locked_turn())
        return "attack"

    with caplog.at_level(logging.WARNING, logger="trek"):
        outcome = h.play(policy=policy)
    assert outcome == "won"
    assert [k for k, _ in emu.acts].count("forced") == 3
    # the policy was never consulted, and no action sent, while locked
    assert asked and all(lock is None for lock in asked)
    assert len(asked) == 3                  # 65 hp / 25 per hit
    msgs = warnings_of(caplog)
    assert not any("frozen screen" in m for m in msgs)
    assert not any("unchanged screen" in m for m in msgs)


def test_locked_turn_names_the_state_it_found():
    emu = FakeEmu()
    h = Harness(emu)
    assert h.locked_turn() is None
    emu.wram["wPlayerSubStatus4"] = RECHARGE
    assert h.locked_turn() == "recharging"
    emu.wram["wPlayerSubStatus4"] = 0
    emu.wram["wPlayerSubStatus5"] = ENCORED
    assert h.locked_turn() == "encored"


def test_encore_confirms_fight_and_lets_the_engine_pick(caplog):
    """ParsePlayerAction replays wLastPlayerMove and never opens
    MoveSelectionScreen, so FIGHT is the whole action -- attack() would
    time out in _wait_move_menu and report a misfire that never happened."""
    emu = FakeEmu()
    emu.arm_encore(2)
    h = Harness(emu)
    with caplog.at_level(logging.WARNING, logger="trek"):
        outcome = h.play(policy=lambda rows, me, en: ("attack", 3))
    assert outcome == "won"
    kinds = [k for k, _ in emu.acts]
    assert kinds[:2] == ["encore-move", "encore-move"]   # engine chose
    assert kinds[2] == "attack"                          # then we did
    assert not any("frozen screen" in m for m in warnings_of(caplog))


# -- (6) unchanged screen: bounded strikes, distinct reason -------------------

def test_unresponsive_menu_bails_with_a_distinct_reason(caplog):
    """The menu answers every press and the battle state never moves --
    exactly the live shape (the screen repainted, so the freeze detector
    never fired and fight() burned its whole budget). Bail with 'stalled',
    bounded, after trying a different action first."""
    emu = FakeEmu(inert=True)
    h = Harness(emu)
    with caplog.at_level(logging.WARNING, logger="trek"):
        outcome = h.play(policy=lambda rows, me, en: "attack",
                         max_frames=500000)
    assert outcome == "stalled"             # not 'timeout', not 'wedged'
    assert emu.frame < 500000               # bailed long before the cap
    sent = [k for k, _ in emu.acts if k == "attack"]
    assert len(sent) <= Battle.STALL_STRIKES
    msgs = warnings_of(caplog)
    assert any("changed nothing" in m for m in msgs)
    assert any("no action changed the battle state" in m for m in msgs)


def test_stalled_run_substitutes_a_different_action():
    """A refused RUN bounces back to the menu without burning a turn
    (BattleMenu_Run .cant_escape -> jp BattleMenu). Even with the WRAM gate
    blind, the breaker must stop re-sending it."""
    emu = FakeEmu(inert=True, hide_trap=True)
    h = Harness(emu, default_act="flee")
    h.play(policy=lambda rows, me, en: "flee", max_frames=500000)
    kinds = [k for k, _ in emu.acts]
    assert kinds.count("run") <= Battle.STALL_SUBSTITUTE + 1
    assert "attack" in kinds                # something else was tried


def test_recovery_backs_out_of_an_open_party_menu_instead_of_mashing():
    """The recovery must never A-mash a nested menu the engine already
    refused: it B's out to the action menu."""
    emu = FakeEmu(wrap=3)
    emu.state, emu.cy, emu.sel = "submenu", 1, 1
    h = Harness(emu)
    what = h._recover_to_action_menu(emu.screen_text())
    assert "party menu" in what
    assert emu.state == "menu"
    assert ("switch", 1) not in emu.acts and \
        ("switch-refused", 1) not in emu.acts


def test_recovery_dismisses_a_refusal_prompt_and_latches():
    emu = FakeEmu(wrap=3)
    emu.state = "refusal"
    h = Harness(emu)
    what = h._recover_to_action_menu(emu.screen_text())
    assert "recalled" in what
    assert emu.state == "menu"
    assert h.switch_refused is True


# -- (7) the diagnostic cap ----------------------------------------------------

def diag_driver():
    d = Driver.__new__(Driver)
    d.emu = SimpleNamespace(screen_text=lambda: ["ONIX 65/65", "RIPTIDE"])
    d.names = SimpleNamespace(moves={1: "HYDRO PUMP"})
    return d, SimpleNamespace(
        me=lambda: {"name": "GYARADOS", "level": 44, "hp": 40,
                    "max_hp": 146, "moves": [(1, 0)]},
        enemy=lambda: {"name": "ONIX", "level": 32, "hp": 65, "max_hp": 65})


def dumps_in(caplog):
    return [m for m in warnings_of(caplog) if "frozen screen" in m]


def test_fight_diagnostic_capped_at_three_per_battle(caplog):
    """It printed 20+ identical dumps per battle live -- once per retried
    fight() on the same still-live battle."""
    d, b = diag_driver()
    with caplog.at_level(logging.WARNING, logger="trek"):
        for _ in range(20):
            d._fight_diag(b, "timeout")
    assert len(dumps_in(caplog)) == Driver.FIGHT_DIAG_CAP == 3
    assert sum(1 for m in warnings_of(caplog) if "cap reached" in m) == 1


def test_fight_diagnostic_budget_resets_for_the_next_battle(caplog):
    d, b = diag_driver()
    with caplog.at_level(logging.WARNING, logger="trek"):
        for _ in range(10):
            d._fight_diag(b, "timeout")
        assert len(dumps_in(caplog)) == 3
        d._fight_diag_prints = 0        # what fight() does when the battle ends
        caplog.clear()
        for _ in range(10):
            d._fight_diag(b, "stalled")
    assert len(dumps_in(caplog)) == 3


def test_fight_reports_a_still_live_battle_and_keeps_the_budget(monkeypatch,
                                                               caplog):
    """An unresolved fight() must SAY the battle is still live -- returning
    a bare 'timeout' is what let pace() walk back into it 60 times -- and
    must not hand the next retry a fresh diagnostic budget."""
    d = Driver.__new__(Driver)
    d.emu = SimpleNamespace(frame=0, screen_text=lambda: ["ONIX"],
                            save=lambda p: None)
    d.names = SimpleNamespace(moves={1: "HYDRO PUMP"})
    d.bdata = None
    d.state_path = None
    d.whiteouts = 0
    d._whiteout_pending = False
    d._pending_nickname = None
    d.default_policy = None
    d.move_changes = []
    d.encounter_events = []
    d._resolve_learn_flow = lambda *a, **k: None
    d._party_moves = lambda: []
    d._diff_learned_moves = lambda *a, **k: None
    d.flush_dialog = lambda *a, **k: None
    d.map_name = lambda: "VICTORY_ROAD_1F"
    d.lead = lambda: None
    d.battle = lambda: 1                       # never resolves
    d.keyboard_open = lambda: False
    d._battle_text_handler = lambda rows: False
    d._resolve_nickname = lambda pending, name: None
    d._turn_policy = lambda b, p, m, disp: ({"turns": 0, "autos": 0}, None)
    d._log_turns = lambda b, state, outcome: (0, 0)
    d._consult_encounter = lambda b, p, m: (None, p)

    fake = SimpleNamespace(
        me=lambda: {"name": "GYARADOS", "level": 44, "hp": 40,
                    "max_hp": 146, "moves": [(1, 0)]},
        enemy=lambda: {"name": "ONIX", "level": 32, "hp": 65, "max_hp": 65},
        play=lambda **kw: "timeout")
    monkeypatch.setattr(trek, "Battle", lambda *a, **k: fake)
    monkeypatch.setattr(trek, "game_state", lambda *a: {
        "player": {"money": 3000}, "party": []})

    with caplog.at_level(logging.WARNING, logger="trek"):
        d.fight()
        d.fight()
        d.fight()
        d.fight()
    assert len(dumps_in(caplog)) == 3           # capped across the retries
    assert any("STILL LIVE" in m for m in warnings_of(caplog))
    assert d.encounter_events[-1]["battle_live"] is True
