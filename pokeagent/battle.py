"""Driving an actual battle: read the engine's cursors, press exactly once,
verify the postcondition.

The rule this module is built around is the one the Crystal harness learned the
expensive way: **never blind-loop A into a menu**. Every screen Sapphire can
put in front of us names itself, and every cursor on those screens is a
variable we can read:

* ``gBattlerControllerFuncs[battler]`` is a function pointer. The symbol table
  turns it into ``sub_802C098`` (the FIGHT/BAG/POKEMON/RUN menu) or
  ``HandleAction_ChooseMove`` (the move picker), so "which menu is up" is an
  exact question rather than a text match.
* ``gActionSelectionCursor[battler]`` and ``gMoveSelectionCursor[battler]``
  are the live cursors. Bit 0 is the column and bit 1 the row of a 2x2 grid;
  LEFT clears bit 0, RIGHT sets it, UP clears bit 1, DOWN sets it
  (src/battle_controller_player.c:393-432). We read the cursor, press the one
  direction that fixes it, and read it back. Counting presses is never done.
* ``gBattlescriptCurrInstr`` points at the running battle-script opcode, and
  ``gBattleScriptingCommandsTable`` turns that opcode into a named handler --
  so "the level-up move-learn box is open" is
  ``atk5A_yesnoboxlearnmove``, not a substring of the message buffer.
* ``gBattleOutcome`` (include/constants/battle.h:68-78) says how the fight
  ended.

Everything that can fail sets :attr:`BattleSession.last_reason` and returns
False. An unexplained falsy return is the single most expensive defect class
in the predecessor project, and a battle loop is exactly where it hides.
"""

import logging
import re
from dataclasses import dataclass

from . import cconst, cstruct, jitter, paths
from .state import NUM_TASKS
from .tactics import Combatant

log = logging.getLogger("pokeagent.battle")

#: ``gActionSelectionCursor`` values, from the switch in
#: src/battle_controller_player.c:376-390. The grid is
#: ``FIGHT BAG / POKEMON RUN``, so bit 0 is the column and bit 1 the row.
ACTION_FIGHT = 0
ACTION_BAG = 1
ACTION_POKEMON = 2
ACTION_RUN = 3
ACTION_NAMES = {0: "FIGHT", 1: "BAG", 2: "POKEMON", 3: "RUN"}

#: The two player-controller functions that own an input cursor.
ACTION_MENU_FUNC = "sub_802C098"
#: The SAFARI ZONE runs a different controller with its own four-option box,
#: `bx_battle_menu_t6_2` (src/battle_controller_safari.c:485, installed just
#: after the menu text is printed). Everything else about it is the same shape:
#: it drives the very same `gActionSelectionCursor` as a 2x2 grid
#: (`…:207-235`), so the cursor driver here works unchanged.
#:
#: Not knowing this symbol is why the densest dex territory in the game was
#: unreachable: `at_action_menu()` tested for `sub_802C098` alone, so inside
#: the Safari Zone every throw_ball/flee/attack failed its own guard before
#: pressing anything.
SAFARI_MENU_FUNC = "bx_battle_menu_t6_2"
#: `BALL {CLEAR_TO} POKEBLOCK \n GO NEAR {CLEAR_TO} RUN`
#: (src/data/battle_strings_en.h:786), so the grid is laid out exactly like
#: FIGHT/BAG over POKEMON/RUN.
SAFARI_BALL, SAFARI_POKEBLOCK, SAFARI_GO_NEAR, SAFARI_RUN = 0, 1, 2, 3
MOVE_MENU_FUNC = "HandleAction_ChooseMove"
#: The battle party menu is a task, not a controller function.
PARTY_MENU_TASK = "HandleBattlePartyMenu"
PARTY_POPUP_TASK = "Task_HandlePopupMenuInput"
#: The OVERWORLD party screen runs a different handler, and the cursor is read
#: the same way for all of them (`sub_806CA00` = gTasks[task].data[3] >> 8,
#: src/party_menu.c:1773-1776). Leaving it out is why reordering the party fell
#: back to guessing press counts.
#: `ePartyMenu` = gSharedMem + 0x1000 (include/ewram.h:80). `struct
#: Unk2001000` has no offset annotations to parse (include/party_menu.h:68-72),
#: so the bytes are counted: u8 unk0, u8 slotId, u8 slotId2.
#:
#: In SWITCH mode the two sprites are NOT interchangeable and the names invite
#: exactly the wrong guess, which I made: `slotId` holds the PINNED first pick
#: and `slotId2` is the cursor that moves. Measured -- pressing UP five times
#: left slotId's sprite at 5 and walked slotId2's 5,4,3,2,1,0.
EPARTY_MENU = 0x1000
EPARTY_SLOT_ID = 1
EPARTY_SLOT_ID2 = 2
PARTY_TASKS = (
    PARTY_MENU_TASK,
    PARTY_POPUP_TASK,
    "HandleDefaultPartyMenu",
    "HandleDefaultPartyMenuInput",
)
#: The battle bag's own list-input task (src/item_menu.c). Its presence,
#: with no palette fade running, is the only honest "the bag will take a
#: press now" test.
BAG_INPUT_TASK = "sub_80A50C8"
#: Battle-script handlers for the two level-up move prompts.
LEARN_PROMPT = "atk5A_yesnoboxlearnmove"
STOP_LEARN_PROMPT = "atk5B_yesnoboxstoplearningmove"

#: One tap. Long enough for JOY_NEW to register, short enough that a repeat
#: never sneaks in and moves a cursor twice.
TAP = "{}:4 .:6"
#: How long to wait for a screen transition before giving up and saying so.
SCREEN_FRAMES = 480
#: How long a whole turn may take to resolve back to a menu (animations,
#: multi-hit moves, an enemy switch and its send-out text).
TURN_FRAMES = 2400

#: Directions that clear/set each cursor bit (battle_controller_player.c:393).
_BIT_KEYS = {
    1: ("LEFT", "RIGHT"),   # bit 0: column
    2: ("UP", "DOWN"),      # bit 1: row
}

_EWRAM_DEFINE = re.compile(
    r"^#define\s+(\w+)\s+.*gSharedMem\s*\+\s*(0[xX][0-9a-fA-F]+)"
)


def ewram_offset(name) -> int:
    """Offset of an ``ewram.h`` overlay from ``gSharedMem``.

    Sapphire reuses one 128K EWRAM block for a dozen different screens, and
    the header spells each overlay as ``(*(struct X *)(gSharedMem + 0x18000))``
    -- a cast, which :mod:`pokeagent.cconst` deliberately refuses to
    evaluate. Scraping the offset out of the header keeps it in one place: the
    decomp.
    """
    path = paths.require(
        paths.INCLUDE / "ewram.h", "header ewram.h", "is the pret/ submodule checked out?"
    )
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = _EWRAM_DEFINE.match(line.strip())
        if m and m.group(1) == name:
            return int(m.group(2), 16)
    raise KeyError(f"no gSharedMem overlay named {name!r} in include/ewram.h")


@dataclass(slots=True)
class Turn:
    """One decision and what it did. The turn log is what makes a lost battle
    auditable afterwards -- ``free_hits`` reading it is how the predecessor
    project finally explained a party wipe."""

    number: int
    action: object
    detail: str
    why: str
    my_mon: str
    their_mon: str
    my_hp_before: int
    their_hp_before: int
    my_hp_after: int = 0
    their_hp_after: int = 0
    ok: bool = True
    note: str = ""

    @property
    def damage_dealt(self) -> int:
        return max(0, self.their_hp_before - self.their_hp_after)

    @property
    def damage_taken(self) -> int:
        return max(0, self.my_hp_before - self.my_hp_after)

    @property
    def kind(self) -> str:
        return self.action[0] if isinstance(self.action, tuple) else str(self.action)

    def __str__(self):
        arg = self.action[1] if isinstance(self.action, tuple) and len(self.action) > 1 else ""
        return (
            f"T{self.number} {self.kind}{f':{arg}' if arg != '' else ''} "
            f"{self.detail} | me {self.my_hp_before}->{self.my_hp_after} "
            f"| {self.their_mon} {self.their_hp_before}->{self.their_hp_after}"
            + ("" if self.ok else "  FAILED")
            + (f"  ({self.note})" if self.note else "")
        )


class BattleSession:
    """One battle, driven through the engine's own state.

    Construct while a battle is live (or just before one starts) and call
    :meth:`play`. Every individual action is also usable on its own, which is
    what makes a battle steppable from the CLI.
    """

    def __init__(self, emu, names, consts, state, tactics):
        self.emu = emu
        self.names = names
        self.consts = consts
        self.state = state
        self.tactics = tactics

        #: Our own PP count per (species, slot, move). See `pp_left`. A dict
        #: on the instance, not a class attribute -- one shared across
        #: sessions would charge one battle's spent PP to the next.
        self._pp: dict = {}
        #: (species, move) pairs that have provably changed nothing. Unlike
        #: `_dead_actions` this survives the battle -- see `futile`.
        self._futile: set = set()

        self.b = consts.battle
        self.max_battlers = self.b["MAX_BATTLERS_COUNT"]
        self.outcome_names = consts.inverse("battle.h", "B_OUTCOME_")

        self.task = cstruct.layout("Task", "task.h")
        self.battle_struct = cstruct.layout("BattleStruct", "battle.h")
        self.sprite_data = cstruct.layout("Sprite", "sprite.h")["data"]
        self.summary_move_index = cstruct.layout(
            "PokemonSummaryScreenStruct", "pokemon_summary_screen.h"
        )["selectedMoveIndex"]
        self.pss_base = self.emu.resolve("gSharedMem") + ewram_offset("pssData")

        self.task_stride = self._stride("gTasks", NUM_TASKS)
        # gSprites is MAX_SPRITES + 1 entries (include/sprite.h), so the stride
        # comes out of the symbol size rather than sizeof(struct Sprite).
        self.sprite_stride = self._stride(
            "gSprites",
            cconst.parse_defines(str(paths.INCLUDE / "sprite.h"))["MAX_SPRITES"] + 1,
        )
        self.script_commands = self.emu.resolve("gBattleScriptingCommandsTable")

        #: HM move ids, straight out of the ROM's TM/HM move table. The HMs are
        #: the tail of that table, and where the tail starts is the distance
        #: between ITEM_HM01 and ITEM_TM01 -- derived, never counted by hand.
        self.hm_moves = self._read_hm_moves()

        self.last_reason = None
        self.last_action_detail = ""
        self.turns: list[Turn] = []
        self._turn_no = 0
        self._learns: list[dict] = []

    # ---- small helpers ---------------------------------------------------

    def _stride(self, symbol, count):
        size = self.emu.sym.size(symbol)
        if not size or size % count:
            raise ValueError(
                f"{symbol} is {size:#x} bytes, not {count} whole entries"
            )
        return size // count

    def _read_hm_moves(self) -> frozenset:
        """``TMHMMoves`` is ``u16[NUM_TMS + NUM_HMS]`` in TM order."""
        size = self.emu.sym.size("TMHMMoves")
        raw = self.emu.read("TMHMMoves", size)
        first_hm = self.consts.items["ITEM_HM01_CUT"] - self.consts.items["ITEM_TM01_FOCUS_PUNCH"]
        return frozenset(
            int.from_bytes(raw[i * 2 : i * 2 + 2], "little")
            for i in range(first_hm, size // 2)
        )

    def _fail(self, reason) -> bool:
        self.last_reason = reason
        log.warning("[battle] %s", reason)
        return False

    def _ok(self) -> bool:
        self.last_reason = None
        return True

    def _wait(self, predicate, timeout_frames=SCREEN_FRAMES, press=None, step=6):
        """Tick until ``predicate()`` is true. Returns True, or False on timeout.

        ``press`` is an input DSL string sent once per poll -- used only to
        advance battle text, never to answer a menu. That distinction is
        enforced here rather than trusted: if the nickname keyboard opens
        mid-wait the pressing STOPS, because a keyboard turns "advance the
        text" into "type a letter". `throw_ball` waits for the bag count to
        drop with press="A:2 .:10", and a successful catch runs straight on
        into the naming screen -- which is how a caught LOTAD ended up called
        AAAAAAAAAA, this project's own stray-A bug in a new place.
        """
        spent = 0
        while spent < timeout_frames:
            if predicate():
                return True
            if self.naming_open():
                return predicate()
            if press:
                self.emu.run_sequence(press)
                spent += 8
            else:
                self.emu.tick(step)
                spent += step
        return predicate()

    def _wait_ready(self, predicate, timeout_frames=SCREEN_FRAMES, hold=3, step=4):
        """Wait until ``predicate()`` has held for ``hold`` consecutive polls.

        Menus fade OUT and then IN, and between the two halves there is a
        window where the menu's task already exists and the fade is
        momentarily idle. A single-sample readiness test passes in that gap,
        the next press lands during the second fade, and the engine discards
        it -- which is precisely how the party cursor "got stuck on slot 0"
        on its first live run. Requiring the condition to persist closes it.
        """
        spent, streak = 0, 0
        while spent < timeout_frames:
            if predicate():
                streak += 1
                if streak >= hold:
                    return True
            else:
                streak = 0
            self.emu.tick(step)
            spent += step
        return False

    # ---- engine introspection ---------------------------------------------

    def active(self) -> bool:
        return self.state.in_battle()

    def outcome(self) -> int:
        return self.emu.u8("gBattleOutcome")

    def outcome_name(self) -> str | None:
        value = self.outcome()
        if not value:
            return None
        return self.outcome_names.get(value, f"B_OUTCOME_{value:#x}")

    def controller(self, battler) -> str:
        """Symbol name of ``gBattlerControllerFuncs[battler]``."""
        ptr = self.emu.u32(("gBattlerControllerFuncs", battler * 4)) & ~1
        sym = self.emu.sym.at(ptr)
        return sym.name if sym else f"{ptr:#010x}"

    def script_command(self) -> str:
        """Named handler for the battle-script opcode currently executing.

        ``gBattlescriptCurrInstr`` points at the opcode byte;
        ``gBattleScriptingCommandsTable`` maps it to a function the symbol
        table names. That is how the level-up move prompt is detected without
        ever looking at rendered text.
        """
        instr = self.emu.u32("gBattlescriptCurrInstr")
        if not instr:
            return "-"
        opcode = self.emu.u8(instr)
        ptr = self.emu.u32(self.script_commands + opcode * 4) & ~1
        sym = self.emu.sym.at(ptr)
        return sym.name if sym else f"op{opcode:#04x}"

    def menu_battler(self) -> int | None:
        """The battler whose controller is asking us for input right now.

        The two cursor arrays are indexed by ``gActiveBattler``, and
        ``gActiveBattler`` is whichever battler's controller the engine is
        currently running -- so the battler sitting in a menu function IS the
        index. In singles that is always 0, but reading it costs nothing and
        keeps doubles from silently driving the wrong cursor.
        """
        for i in range(self.max_battlers):
            if self.controller(i) in (
                ACTION_MENU_FUNC, MOVE_MENU_FUNC, SAFARI_MENU_FUNC
            ):
                return i
        return None

    def at_action_menu(self) -> bool:
        return any(
            self.controller(i) in (ACTION_MENU_FUNC, SAFARI_MENU_FUNC)
            for i in range(self.max_battlers)
        )

    def at_safari_menu(self) -> bool:
        """Is the SAFARI four-option box the thing on screen?"""
        return any(
            self.controller(i) == SAFARI_MENU_FUNC
            for i in range(self.max_battlers)
        )

    def safari(self) -> bool:
        """Are we in a Safari Zone encounter?

        Read from the battle type bit rather than the map, because the Safari
        rules are what change: there is no party to send out, the only ball is
        the Safari Ball, and the menu is BALL/POKEBLOCK/GO NEAR/RUN.
        """
        try:
            return "safari" in (self.state.battle().kinds or ())
        except Exception:  # noqa: BLE001 - an unreadable type is not a safari
            return False

    def at_move_menu(self) -> bool:
        return any(
            self.controller(i) == MOVE_MENU_FUNC for i in range(self.max_battlers)
        )

    def at_party_menu(self) -> bool:
        return PARTY_MENU_TASK in self.state.tasks()

    def at_party_popup(self) -> bool:
        return PARTY_POPUP_TASK in self.state.tasks()

    def at_learn_prompt(self) -> bool:
        return self.script_command() in (LEARN_PROMPT, STOP_LEARN_PROMPT)

    def action_cursor(self, battler) -> int:
        return self.emu.u8(("gActionSelectionCursor", battler))

    def move_cursor(self, battler) -> int:
        return self.emu.u8(("gMoveSelectionCursor", battler))

    def doubles(self) -> bool:
        """Is this a DOUBLE battle? Then the player owns battlers 0 AND 2."""
        return bool(self.emu.u16("gBattleTypeFlags")
                    & self.b["BATTLE_TYPE_DOUBLE"])

    def my_battlers(self) -> tuple:
        """The battler slots this side controls, engine order."""
        return (0, 2) if self.doubles() else (0,)

    def sent_out(self, choice) -> bool:
        """Did the engine put party slot `choice` onto the field?

        `gBattlerPartyIndexes[0]` alone answers this only in a SINGLE battle.
        Tate & Liza is a double, so a replacement for the right-hand slot lands
        in `gBattlerPartyIndexes[2]` and checking index 0 declares the switch
        failed while the mon is standing on the field. That misread the whole
        gym battle as "stuck".
        """
        return any(self.party_index_of(b) == choice for b in self.my_battlers())

    def party_index_of(self, battler) -> int:
        return self.emu.u16(("gBattlerPartyIndexes", battler * 2))

    # ---- reading the fight --------------------------------------------------

    def battler(self, index) -> Combatant | None:
        """The battler at `index`, or None when there is no such battler.

        `menu_battler()` answers None whenever the action menu is not the thing
        on screen, and passing that straight through raised `NoneType * int`
        from inside the struct read -- a crash where the honest answer is
        "nobody is choosing right now".
        """
        if index is None:
            return None
        return self.tactics.read_battler(index)

    def frame(self) -> dict:
        """Everything a policy needs, in one read.

        ``moves`` is in ENGINE SLOT ORDER and each entry carries its slot, so a
        policy that indexes the list still gets the right move. Sorting happens
        in the caller, never here.
        """
        if not self.active():
            return {
                "active": False, "me": None, "enemy": None, "party": [],
                "bag": {}, "turn": self._turn_no, "wild": False,
                "can_switch": [], "moves": [],
                "outcome": self.outcome_name(),
            }
        me = self.battler(0)
        enemy = self.battler(1)
        flags = self.emu.u16("gBattleTypeFlags")
        active_index = self.party_index_of(0)
        party = self.state.party()
        return {
            "active": True,
            "me": self._mon_view(me, active_index),
            "enemy": self._mon_view(enemy, None),
            "party": [
                {
                    "index": i,
                    "nickname": m.nickname,
                    "species": self.names.species(m.species) if m.species else "EGG",
                    "level": m.level,
                    "hp": m.hp,
                    "max_hp": m.max_hp,
                    "status": m.status_name,
                    "egg": m.is_egg,
                    "active": i == active_index,
                }
                for i, m in enumerate(party)
            ],
            "bag": self.state.bag(),
            "turn": self._turn_no,
            "wild": not bool(flags & self.b["BATTLE_TYPE_TRAINER"]),
            "can_switch": self.switchable(party, active_index),
            "moves": [
                {
                    "slot": slot,
                    "id": mid,
                    "name": self.names.move(mid),
                    "pp": pp,
                    "power": self.names.move_data(mid).power,
                    "type": self.names.type(self.names.move_data(mid).type),
                }
                for slot, (mid, pp) in enumerate(zip(me.moves, me.pp))
                if mid
            ],
            "outcome": self.outcome_name(),
            "menu": self.menu_name(),
        }

    def menu_name(self) -> str:
        if self.at_move_menu():
            return "moves"
        if self.at_action_menu():
            return "action"
        if self.at_party_popup():
            return "party_popup"
        if self.at_party_menu():
            return "party"
        if self.at_learn_prompt():
            return "learn"
        return "text"

    def _mon_view(self, mon: Combatant, party_index) -> dict:
        return {
            "species": mon.name,
            "nickname": mon.nickname,
            "level": mon.level,
            "hp": mon.hp,
            "max_hp": mon.max_hp,
            "types": [self.names.type(t) for t in dict.fromkeys(mon.types)],
            "status": self.tactics.status_names(mon),
            "stat_stages": list(mon.stat_stages),
            "ability": self.names.ability(mon.ability),
            "party_index": party_index,
            "moves": [
                {"slot": s, "name": self.names.move(m), "pp": p}
                for s, (m, p) in enumerate(zip(mon.moves, mon.pp))
                if m
            ],
        }

    def switchable(self, party=None, active_index=None) -> list:
        """Party indexes the engine would actually accept as a switch-in.

        Alive, not an egg, and not the mon already standing on the field.

        ``gBattlerPartyIndexes[0]`` alone is NOT a safe exclusion. Verified
        live: when the active mon faints the engine REORDERS ``gPlayerParty``
        (the fainted mon moves to slot 0) but leaves ``gBattlerPartyIndexes[0]``
        pointing at slot 1 -- which is now a perfectly healthy benched mon.
        Excluding it blindly returned an empty list at the exact moment a
        replacement was required, so the forced switch reported "everything is
        fainted" with a full-HP mon sitting on the bench. Hence the field mon
        is skipped only while it is actually still standing.
        """
        if party is None:
            party = self.state.party()
        if active_index is None:
            active_index = self.party_index_of(0)
        on_field_alive = self.battler(0).hp > 0
        return [
            i for i, m in enumerate(party)
            if not m.is_egg and m.hp
            and not (i == active_index and on_field_alive)
        ]

    # ---- cursor driving -------------------------------------------------------

    def _drive_grid_cursor(self, read, target, label, max_steps=6) -> bool:
        """Walk a 2x2 menu cursor to ``target`` by reading it, not counting.

        Each press fixes exactly one bit and is verified. A press that does not
        move the cursor is reported rather than retried forever -- that is the
        engine refusing (a move slot past the moveset, a greyed-out RUN), and
        mashing at it is how the predecessor burned 90k frames on one turn.
        """
        for _ in range(max_steps):
            current = read()
            if current == target:
                return True
            diff = current ^ target
            bit = 1 if diff & 1 else 2
            clear_key, set_key = _BIT_KEYS[bit]
            key = set_key if target & bit else clear_key
            self.emu.run_sequence(jitter.sequence(TAP.format(key)))
            if read() == current:
                return self._fail(
                    f"{label} cursor stuck at {current} (wanted {target}): "
                    f"{key} did not move it -- the engine is refusing that cell"
                )
        return self._fail(
            f"{label} cursor never reached {target} (still {read()}) after "
            f"{max_steps} presses"
        )

    def _choose_action(self, action, label) -> bool:
        """Put the battle menu on ``action`` and confirm it."""
        battler = self.menu_battler()
        if battler is None or not self.at_action_menu():
            return self._fail(
                f"cannot pick {label}: the action menu is not up "
                f"(controller 0 is {self.controller(0)})"
            )
        if not self._drive_grid_cursor(
            lambda: self.action_cursor(battler), action, "action"
        ):
            return False
        self.emu.run_sequence(jitter.sequence(TAP.format("A")))
        if not self._wait(
            lambda: not self.at_action_menu(), timeout_frames=SCREEN_FRAMES
        ):
            return self._fail(
                f"pressed A on {label} but the action menu is still up "
                f"(cursor {self.action_cursor(battler)})"
            )
        return self._ok()

    # ---- actions -----------------------------------------------------------------

    # ---- our own PP ledger ----------------------------------------------
    #
    # The engine's PP byte is the truth and we still keep our own count, for
    # one reason: a decision is made from an ANALYSIS, and an analysis is a
    # snapshot. Anything that reads a stale, cached or wrong-battler snapshot
    # can offer a move that has nothing left, and the visible result is a run
    # that "keeps trying to call PROTECT when there's no PP left" -- reported
    # from watching the screen, which is the only place that symptom shows.
    #
    # The ledger is seeded from the live read, decremented on every move this
    # harness actually sends, and RESEEDED whenever the live read comes back
    # higher than the count -- that is a Centre visit, an Ether, or simply a
    # different mon in the slot, and a monotonic counter would otherwise stay
    # wrong for the rest of the run.

    def _pp_table(self, me) -> str:
        """`MOVE pp/…` for the whole moveset, for a log line that settles it."""
        bits = []
        for i, mid in enumerate(list(getattr(me, "moves", ()))[:4]):
            if not mid:
                continue
            bits.append(f"{self.names.move(mid)} {self.pp_left(me, i)}")
        return ", ".join(bits) or "no moves"

    def _pp_key(self, me, slot):
        """Identity of a move slot that survives switches.

        Keyed on the SPECIES plus the move id, not the battler index: index 0
        is a different mon after every switch, and a ledger keyed on it would
        charge one mon's spent PP to another.
        """
        moves = getattr(me, "moves", ()) or ()
        move = moves[slot] if slot < len(moves) else 0
        return (getattr(me, "species", 0), slot, move)

    def pp_left(self, me, slot) -> int:
        """PP remaining in this slot, by our count and the engine's.

        The lower of the two, so a wrong high read cannot spend a turn -- and
        a read that is higher than our count reseeds it, so a restore is
        believed immediately.
        """
        live = 0
        pp = getattr(me, "pp", ()) or ()
        if slot < len(pp):
            live = int(pp[slot] or 0)
        key = self._pp_key(me, slot)
        mine = self._pp.get(key)
        if mine is None:
            self._pp[key] = live
            return live
        # Distinguishing a RESTORE from a lagging read is the whole difficulty,
        # and the first attempt got it wrong in a way the tests caught: any
        # `live > mine` was treated as a restore, which is exactly the state
        # one frame after we send a move -- so the ledger reseeded itself on
        # every use and vetoed nothing.
        #
        # A restore is a JUMP: a Centre or an Ether puts the count back to the
        # move's maximum, many points at once. A lag is off by one, because we
        # spend one at a time. So only a gap wider than one is believed.
        #
        # The known cost: a one-PP move restored from empty looks like a lag
        # and stays vetoed until something else reseeds it. No Gen-3 move has
        # a maximum that low, so nothing real is lost.
        if live - mine > 1:
            self._pp[key] = live
            return live
        return min(live, mine)

    def _pp_spend(self, me, slot) -> None:
        """One move sent, one PP gone by our count."""
        key = self._pp_key(me, slot)
        if key in self._pp:
            self._pp[key] = max(0, self._pp[key] - 1)

    def usable_slots(self, me) -> list:
        """Slots holding a real move with PP left, by both counts."""
        moves = getattr(me, "moves", ()) or ()
        return [i for i in range(min(4, len(moves)))
                if moves[i] and self.pp_left(me, i) > 0]

    def attack(self, slot=None) -> bool:
        """FIGHT, then move ``slot``. ``None`` asks tactics for the best move.

        Verifies the move slot is real and has PP BEFORE pressing A, because
        the move picker silently refuses an empty slot and leaves the cursor
        exactly where it was -- indistinguishable from a swallowed press.
        """
        if not self.at_action_menu() and not self.await_action_menu():
            return self._fail(
                f"attack() needs the action menu; it is {self.menu_name()}"
                + (f" ({self.last_reason})" if self.last_reason else "")
            )
        battler = self.menu_battler()
        me = self.battler(battler)
        if slot is None:
            analysis = self.tactics.outlook()
            if analysis is None:
                return self._fail(
                    f"attack() had no slot and outlook() declined: "
                    f"{self.tactics.last_outlook_reason}"
                )
            action, why = self.tactics.recommend(analysis)
            if not (isinstance(action, tuple) and action[0] == "attack"):
                # The caller asked for an attack; honour that but say what
                # tactics would have preferred instead of silently ignoring it.
                # Our ledger, not just the analysis: the analysis is the
                # snapshot that can be stale.
                live_ok = set(self.usable_slots(me))
                usable = [m for m in analysis["moves"]
                          if m["pp"] and m["kind"] != "empty"
                          and m["slot"] in live_ok]
                # A move that has already proven to change nothing goes last.
                usable.sort(key=lambda m: (
                    self.futile(("attack", m["slot"])),
                    -(m.get("damage_max") or 0),
                ))
                if not usable:
                    return self._fail(
                        "attack() found no move with PP left "
                        f"(tactics wanted {action!r}: {why})"
                    )
                slot = usable[0]["slot"]
                log.info(
                    "[battle] harness chose attack slot %d; tactics preferred "
                    "%r (%s) but the caller asked to attack", slot, action, why
                )
            else:
                slot = action[1]
                log.info("[battle] harness chose attack slot %d: %s", slot, why)
        if not 0 <= slot < 4:
            return self._fail(f"move slot {slot} is out of range 0-3")
        if not me.moves[slot]:
            return self._fail(
                f"move slot {slot} is empty (moveset: "
                f"{[self.names.move(m) for m in me.moves if m]})"
            )
        if not self.pp_left(me, slot):
            # SUBSTITUTE, do not merely refuse.
            #
            # Refusing was correct and still wasted the turn: the caller had
            # already decided, the loop retried, and from outside it looks
            # exactly like "we are trying to use a move with no PP left" --
            # which is what kept being reported. Whoever asked was wrong about
            # this slot; the honest response is to play the best move that
            # actually has PP and say loudly who asked for what.
            spare = self.usable_slots(me)
            asked = self.names.move(me.moves[slot]) if me.moves[slot] else "?"
            if not spare:
                # NOTHING HAS PP -> STRUGGLE, which is a real move the engine
                # substitutes for whatever slot you pick. Refusing here was a
                # dead end: the caller retries, the retry refuses again, and a
                # trainer battle that cannot be fled burns the whole budget
                # without a turn being taken. That is exactly what happened at
                # the Seafloor Cavern boss with a healthy bench behind a dry
                # lead.
                log.warning(
                    "[battle] %s (slot %d) and every other move are dry -- "
                    "pressing on for STRUGGLE (%s)", asked, slot,
                    self._pp_table(me),
                )
            best = max(
                spare,
                key=lambda i: (self.names.move_data(me.moves[i]).power or 0),
            ) if spare else slot
            if spare:
                log.warning(
                    "[battle] asked for %s (slot %d) with no PP -- using %s "
                    "instead (%s)", asked, slot,
                    self.names.move(me.moves[best]), self._pp_table(me),
                )
            slot = best

        if not self._choose_action(ACTION_FIGHT, "FIGHT"):
            return False
        if not self._wait(self.at_move_menu):
            return self._fail(
                f"FIGHT was confirmed but the move picker never opened "
                f"(controller {self.controller(battler)})"
            )
        if not self._drive_grid_cursor(
            lambda: self.move_cursor(battler), slot, "move"
        ):
            return False
        name = self.names.move(me.moves[slot])
        self.emu.run_sequence(jitter.sequence(TAP.format("A")))
        if not self._wait(lambda: not self.at_move_menu()):
            return self._fail(
                f"pressed A on {name} (slot {slot}) but the move picker is "
                f"still open -- the engine refused the selection"
            )
        self.last_action_detail = f"{name}#{slot}"
        self._pp_spend(me, slot)
        return self._ok()

    def switch_to(self, party_index) -> bool:
        """POKEMON -> that mon -> SHIFT. Verified against
        ``gBattlerPartyIndexes``, which is the engine's own answer to "who is
        out"."""
        if not self.at_action_menu() and not self.await_action_menu():
            return self._fail(
                f"switch_to() needs the action menu; it is {self.menu_name()}"
            )
        party = self.state.party()
        if not 0 <= party_index < len(party):
            return self._fail(
                f"party index {party_index} does not exist (party has "
                f"{len(party)})"
            )
        target = party[party_index]
        if target.is_egg:
            return self._fail(f"party slot {party_index} is an EGG")
        if not target.hp:
            return self._fail(
                f"{target.nickname or self.names.species(target.species)} "
                f"(slot {party_index}) has fainted"
            )
        if self.party_index_of(0) == party_index:
            return self._fail(
                f"party slot {party_index} is already the active battler"
            )
        forced = self.at_party_menu()
        if not forced:
            if not self._choose_action(ACTION_POKEMON, "POKEMON"):
                return False
            if not self._wait_ready(self._party_ready):
                return self._fail(
                    "POKEMON was confirmed but the battle party menu never "
                    f"became ready for input (tasks: {self.state.tasks()}, "
                    f"fading={self._fading()})"
                )
        if not self._drive_party_cursor(party_index):
            return False
        self.emu.run_sequence(jitter.sequence(TAP.format("A")))
        if not self._wait(self.at_party_popup):
            return self._fail(
                f"selected party slot {party_index} but the SHIFT/SUMMARY "
                "popup never appeared -- the engine refused that mon"
            )
        # SHIFT is the first row of sBattlePartyPopupMenus for a healthy,
        # non-active mon (src/battle_party_menu.c:442-463).
        self.emu.run_sequence(jitter.sequence(TAP.format("A")))
        # ASK EVERY BATTLER THIS SIDE OWNS, and give the send-out animation
        # room to finish. Reading gBattlerPartyIndexes[0] alone is wrong in a
        # double battle, and the swap does not land the instant the menu
        # closes -- so a switch that plainly worked reported "gBattlerPartyIndexes[0]
        # is still 5" and the harness fell back to fleeing, which a trainer
        # battle refuses. That pair burned the Seafloor Cavern boss twice.
        if not self._wait(
            lambda: self.sent_out(party_index) or not self.active(),
            timeout_frames=TURN_FRAMES * 2,
            press="A:2 .:10",
        ):
            return self._fail(
                f"confirmed SHIFT to slot {party_index} but "
                f"gBattlerPartyIndexes is "
                f"{[self.party_index_of(b) for b in self.my_battlers()]}"
            )
        self.last_action_detail = (
            f"{target.nickname or self.names.species(target.species)}#{party_index}"
        )
        return self._ok()

    def _party_cursor(self) -> int | None:
        """Selected party slot on the battle party menu.

        The engine keeps it on the cursor SPRITE: ``sub_806CA38`` reads
        ``gSprites[gTasks[task].data[3] >> 8].data[0]``
        (src/party_menu.c). Indirect, but it is the number the engine itself
        acts on, which beats counting our own presses.
        """
        # SWITCH mode is a different cursor, and it is `slotId2` -- see the
        # constants above for the measurement. `SetupDefaultPartyMenuSwitchPokemon`
        # (src/party_menu.c:1790-1805) also sets the original task's func to
        # TaskDummy, so the task-name scan below finds nothing at all here and
        # the drive to slot 0 reported the cursor unreadable.
        if "HandlePartyMenuSwitchPokemonInput" in self.state.tasks():
            slot_id = self.emu.u8(
                self.emu.resolve("gSharedMem") + EPARTY_MENU + EPARTY_SLOT_ID2
            )
            sprite = (self.emu.resolve("gSprites")
                      + slot_id * self.sprite_stride)
            return self.emu.s16(sprite + self.sprite_data)
        base = self.emu.resolve("gTasks")
        for i in range(NUM_TASKS):
            addr = base + i * self.task_stride
            if not self.emu.u8(addr + self.task["isActive"]):
                continue
            ptr = self.emu.u32(addr + self.task["func"]) & ~1
            sym = self.emu.sym.at(ptr)
            if not sym or sym.name not in PARTY_TASKS:
                continue
            data3 = self.emu.u16(addr + self.task["data"] + 3 * 2)
            sprite_id = data3 >> 8
            sprite = self.emu.resolve("gSprites") + sprite_id * self.sprite_stride
            return self.emu.s16(sprite + self.sprite_data)
        return None

    def _party_ready(self) -> bool:
        """The party menu is up AND will accept a press (no fade in flight)."""
        return (
            not self._fading()
            and self.at_party_menu()
            and self._party_cursor() is not None
        )

    def _drive_party_cursor(self, target, max_steps=12) -> bool:
        """Walk the party cursor to ``target``, reading it every step.

        The row past the last mon is CANCEL, which reports as slot 7 rather
        than a party index (verified live: with two mons, DOWN goes 0 -> 1 ->
        7). Comparing numerically still converges, because overshooting to 7
        makes the next step go UP.
        """
        if not self._wait_ready(self._party_ready):
            return self._fail(
                "the battle party menu is up but its cursor sprite is not "
                f"readable (fading={self._fading()}) -- refusing to press A blind"
            )
        for _ in range(max_steps):
            current = self._party_cursor()
            if current == target:
                return True
            key = "UP" if current == 7 or target < current else "DOWN"
            self.emu.run_sequence(jitter.sequence(TAP.format(key)))
            self._wait_ready(self._party_ready, timeout_frames=SCREEN_FRAMES // 2)
            if self._party_cursor() == current:
                return self._fail(
                    f"party cursor stuck on slot {current} (wanted {target}): "
                    f"{key} did not move it"
                )
        return self._fail(
            f"party cursor never reached slot {target} "
            f"(still {self._party_cursor()})"
        )

    # ---- the bag ------------------------------------------------------------------

    def _item_id(self, name):
        """Loose item-name lookup; see :meth:`Tactics.item_id`."""
        return self.tactics.item_id(name)

    def _bag_slot(self, item_id):
        """``(pocket_index, slot)`` of an item in the live bag, or None.

        ``gBagPockets[i].itemSlots`` points into the save block, so the pocket
        the menu shows can be matched to the save-block pocket by POINTER --
        no pocket-order table to get out of date.
        """
        pockets = self.emu.sym.size("gBagPockets")
        stride = pockets // 5
        base = self.emu.resolve("gBagPockets")
        for i in range(5):
            slots = self.emu.u32(base + i * stride)
            capacity = self.emu.u8(base + i * stride + 4)
            for s in range(capacity):
                if self.emu.u16(slots + s * 4) == item_id:
                    return (i, s)
        return None

    def _bag_quantity(self, item_id) -> int:
        found = self._bag_slot(item_id)
        if found is None:
            return 0
        pocket, slot = found
        stride = self.emu.sym.size("gBagPockets") // 5
        slots = self.emu.u32(self.emu.resolve("gBagPockets") + pocket * stride)
        return self.emu.u16(slots + slot * 4 + 2)

    def _bag_pocket(self) -> int:
        """``sCurrentBagPocket``: which pocket the bag UI is showing."""
        return self.emu.u8("sCurrentBagPocket")

    def _bag_cursor(self, pocket) -> int:
        """``scrollTop + cursorPos`` -- the engine's own selected index
        (src/item_menu.c:446)."""
        base = self.emu.resolve("gBagPocketScrollStates") + pocket * 4
        return self.emu.u8(base + 1) + self.emu.u8(base)

    def _fading(self) -> bool:
        """``gPaletteFade.active``. The bag draws behind a fade, and every
        D-pad press that lands during it is DISCARDED -- ``sub_80A50C8`` does
        nothing at all while the fade runs (src/item_menu.c)."""
        return bool(self.emu.u8(self.emu.resolve("gPaletteFade") + 7) & 0x80)

    def _at_bag(self) -> bool:
        """The bag is up AND ready to take input.

        Both halves matter. Testing only "the bag is up" is what made the
        first live run fail: the bag task exists a full 44 frames before the
        open fade finishes, and every press in that window is swallowed, so
        the cursor walker correctly -- but pointlessly -- reported a stuck
        cursor. Readiness is ``sub_80A50C8`` (the list's own input handler)
        running with no fade in flight.
        """
        return not self._fading() and BAG_INPUT_TASK in self.state.tasks()

    def _open_bag(self) -> bool:
        if not self._choose_action(ACTION_BAG, "BAG"):
            return False
        if not self._wait_ready(self._at_bag):
            return self._fail(
                "BAG was confirmed but the bag never became ready for input "
                f"(pocket {self._bag_pocket()}, tasks {self.state.tasks()})"
            )
        return True

    def _bag_tap(self, key) -> bool:
        """One D-pad press, then wait for the list to accept input again.

        Changing pocket hands the task to ``sub_80A4F68`` for the slide
        animation; pressing again before it hands back is a lost press.
        """
        self.emu.run_sequence(jitter.sequence(TAP.format(key)))
        return self._wait_ready(self._at_bag, timeout_frames=SCREEN_FRAMES)

    def _drive_bag(self, item_id, item_name) -> bool:
        found = self._bag_slot(item_id)
        if found is None:
            return self._fail(f"{item_name} is not in the bag")
        pocket, slot = found
        # Pockets change with LEFT/RIGHT, not the shoulder buttons: the bag's
        # D-pad handler routes them through sub_80A4F0C (src/item_menu.c).
        for _ in range(8):
            before = self._bag_pocket()
            if before == pocket:
                break
            key = "RIGHT" if before < pocket else "LEFT"
            self._bag_tap(key)
            if self._bag_pocket() == before:
                return self._fail(
                    f"bag pocket stuck on {before} (wanted {pocket} for "
                    f"{item_name}): {key} did not change it"
                )
        if self._bag_pocket() != pocket:
            return self._fail(
                f"never reached bag pocket {pocket} for {item_name} "
                f"(still {self._bag_pocket()})"
            )
        for _ in range(32):
            current = self._bag_cursor(pocket)
            if current == slot:
                return True
            key = "DOWN" if slot > current else "UP"
            self._bag_tap(key)
            if self._bag_cursor(pocket) == current:
                return self._fail(
                    f"bag cursor stuck on row {current} (wanted {slot} for "
                    f"{item_name})"
                )
        return self._fail(
            f"bag cursor never reached row {slot} for {item_name} "
            f"(still {self._bag_cursor(pocket)})"
        )

    def use_item(self, name, target=None) -> bool:
        """BAG -> pocket -> item -> USE, and a party target when one is asked
        for. Verified by the bag count dropping, which is the only thing that
        proves the turn was actually spent."""
        if not self.at_action_menu() and not self.await_action_menu():
            return self._fail(
                f"use_item() needs the action menu; it is {self.menu_name()}"
            )
        item_id = self._item_id(name)
        if not item_id:
            return self._fail(f"{name!r} is not an item this ROM knows about")
        before = self._bag_quantity(item_id)
        if not before:
            return self._fail(f"{name} is not in the bag")
        if target is None:
            target = self.party_index_of(0)
        if not self._open_bag():
            return False
        if not self._drive_bag(item_id, name):
            self._back_out()
            return False
        self.emu.run_sequence(jitter.sequence(TAP.format("A")))
        # The popup's first row is USE for anything battle-usable
        # (sItemPopupMenuActions, src/item_menu.c).
        self.emu.run_sequence(jitter.sequence(TAP.format("A")))
        if self._wait_ready(self._party_ready, timeout_frames=SCREEN_FRAMES // 2):
            if not self._drive_party_cursor(target):
                self._back_out()
                return False
            self.emu.run_sequence(jitter.sequence(TAP.format("A")))
        if not self._wait(
            lambda: self._bag_quantity(item_id) < before or not self.active(),
            timeout_frames=TURN_FRAMES,
            press="A:2 .:10",
        ):
            return self._fail(
                f"used {name} but the bag still holds {before} of it -- the "
                "engine did not accept the item, and the turn was not spent"
            )
        self.last_action_detail = name
        return self._ok()

    def safari_ball(self) -> bool:
        """Throw the Safari Ball -- the only ball a Safari encounter has.

        Nothing goes through the bag here: the BALL option IS the throw
        (`bx_battle_menu_t6_2` emits return value 5 for cursor 0,
        src/battle_controller_safari.c:207-228). `throw_ball` routes here on
        its own, so callers never have to know which kind of battle they are in.
        """
        if not self.at_action_menu() and not self.await_action_menu():
            return self._fail(
                f"safari_ball() needs the action menu; it is {self.menu_name()}"
            )
        if not self._choose_action(SAFARI_BALL, "BALL"):
            return False
        # The throw plays out and either catches or the mon flees; both end the
        # battle, so wait for that rather than for a menu that may never return.
        self._wait(
            lambda: not self.active() or self.outcome(),
            timeout_frames=TURN_FRAMES,
            press="A:2 .:10",
        )
        return self._ok()

    def safari_go_near(self) -> bool:
        """GO NEAR: the only lever that raises Safari catch odds.

        `HandleAction_GoNear` (pret/src/battle_main.c:5601-5626) adds
        `gUnknown_081FA71B[goNearCounter]` to `safariCatchFactor` and
        `gUnknown_081FA71F[goNearCounter]` to `safariFleeRate`, each capped at
        20, with the counter rising to 3. The tables are 4,3,2,1 for the bonus
        and a flat 4,4,4,4 for the penalty (pret/data/btl_attrs.s:380-391), so
        the FIRST approach trades 4 for 4 and every one after it trades less
        for the same -- which is why the policy takes exactly one.

        Unlike a throw this does not end the battle, so it settles back to the
        action menu rather than waiting for an outcome.
        """
        if not self.at_action_menu() and not self.await_action_menu():
            return self._fail(
                f"safari_go_near() needs the action menu; it is "
                f"{self.menu_name()}"
            )
        if not self._choose_action(SAFARI_GO_NEAR, "GO NEAR"):
            return False
        self._wait(
            lambda: self.at_action_menu() or not self.active() or self.outcome(),
            timeout_frames=TURN_FRAMES,
            press="A:2 .:10",
        )
        return self._ok()

    def safari_flee(self) -> bool:
        """RUN, on the Safari grid. Always permitted -- there is no trainer."""
        if not self.at_action_menu() and not self.await_action_menu():
            return self._fail(
                f"safari_flee() needs the action menu; it is {self.menu_name()}"
            )
        if not self._choose_action(SAFARI_RUN, "RUN"):
            return False
        self._wait(
            lambda: not self.active() or self.outcome(),
            timeout_frames=TURN_FRAMES,
            press="A:2 .:10",
        )
        return self._ok()

    def throw_ball(self, name=None) -> bool:
        """Same path as :meth:`use_item`, minus the target: a ball is thrown at
        the battler already on screen.

        In the SAFARI ZONE there is no bag and only one kind of ball, so this
        hands off to `safari_ball`. Callers stay ignorant of the difference,
        which is the point -- the catching policy should not have to ask.
        """
        if self.safari() or self.at_safari_menu():
            return self.safari_ball()
        if not self.at_action_menu() and not self.await_action_menu():
            return self._fail(
                f"throw_ball() needs the action menu; it is {self.menu_name()}"
            )
        if name is None:
            balls = self.state.bag().get("poke_balls") or {}
            if not balls:
                return self._fail("no balls in the bag")
            name = min(
                balls,
                key=lambda n: self.names.item_data(self._item_id(n)).price,
            )
        item_id = self._item_id(name)
        if not item_id:
            return self._fail(f"{name!r} is not an item this ROM knows about")
        # "Is this a ball?" is answered by the ROM, not by a transcribed
        # pocket id: whatever pocket ITEM_POKE_BALL lives in is the ball
        # pocket (the enum itself is in include/item.h, which cconst does not
        # read -- it holds no #defines).
        ball_pocket = self.names.item_data(
            self.consts.items["ITEM_POKE_BALL"]
        ).pocket
        pocket = self.names.item_data(item_id).pocket
        if pocket != ball_pocket:
            return self._fail(
                f"{name} is not a ball: it sits in bag pocket {pocket}, balls "
                f"are in pocket {ball_pocket}"
            )
        before = self._bag_quantity(item_id)
        if not before:
            return self._fail(f"{name} is not in the bag")
        if not self._open_bag():
            return False
        if not self._drive_bag(item_id, name):
            self._back_out()
            return False
        self.emu.run_sequence(jitter.sequence(TAP.format("A")))
        self.emu.run_sequence(jitter.sequence(TAP.format("A")))
        if not self._wait(
            lambda: self._bag_quantity(item_id) < before or not self.active(),
            timeout_frames=TURN_FRAMES,
            press="A:2 .:10",
        ):
            return self._fail(
                f"threw {name} but the bag count never dropped -- no ball left "
                "the bag"
            )
        self.last_action_detail = name
        return self._ok()

    def can_flee(self) -> tuple:
        """``(bool, reason)`` -- a port of ``CanRunFromBattle``
        (src/battle_main.c:4121-4180), so a hopeless RUN is refused BEFORE it
        costs a turn instead of after.

        The three early exits come first in the engine too: a
        CAN_ALWAYS_RUN held item, a link battle and RUN AWAY all beat every
        trap below them.
        """
        flags = self.emu.u16("gBattleTypeFlags")
        if flags & self.b["BATTLE_TYPE_TRAINER"]:
            return (False, "cannot run from a trainer battle")
        me = self.battler(0)
        abilities = self.consts.ns("abilities.h")
        holds = self.consts.ns("hold_effects.h")
        if me.item and self.names.item_data(me.item).hold_effect == holds["HOLD_EFFECT_CAN_ALWAYS_RUN"]:
            return (True, None)
        if flags & self.b["BATTLE_TYPE_LINK"]:
            return (True, None)
        if me.ability == abilities["ABILITY_RUN_AWAY"]:
            return (True, None)
        for i in range(self.emu.u8("gBattlersCount")):
            if i % 2 == 0:            # same side as the player
                continue
            them = self.battler(i)
            if them.ability == abilities["ABILITY_SHADOW_TAG"]:
                return (False, f"{them.name}'s SHADOW TAG prevents escape")
            if (
                them.ability == abilities["ABILITY_ARENA_TRAP"]
                and me.ability != abilities["ABILITY_LEVITATE"]
                and self.consts.ns("pokemon.h")["TYPE_FLYING"] not in me.types
            ):
                return (False, f"{them.name}'s ARENA TRAP prevents escape")
        if me.status2 & (self.b["STATUS2_ESCAPE_PREVENTION"] | self.b["STATUS2_WRAPPED"]):
            return (False, "trapped (MEAN LOOK / WRAP): STATUS2 blocks escape")
        if self.emu.u32(("gStatuses3", 0)) & self.b["STATUS3_ROOTED"]:
            return (False, "INGRAIN has me rooted in place")
        if flags & self.b["BATTLE_TYPE_FIRST_BATTLE"]:
            return (
                False,
                "the tutorial first battle never allows a run "
                "(CanRunFromBattle, src/battle_main.c:4174)",
            )
        return (True, None)

    def flee(self) -> bool:
        """RUN. A refused escape bounces straight back to the action menu
        without burning a turn, so that is what we check for -- looping on a
        refusal is how a battle silently eats its frame budget."""
        if self.safari() or self.at_safari_menu():
            return self.safari_flee()
        allowed, why = self.can_flee()
        if not allowed:
            return self._fail(why)
        if not self._choose_action(ACTION_RUN, "RUN"):
            return False
        ended = self._wait(
            lambda: not self.active() or self.outcome(),
            timeout_frames=TURN_FRAMES,
            press="A:2 .:10",
        )
        if not ended:
            return self._fail(
                "RUN was confirmed but the battle is still running -- the "
                "escape was refused (trapped, or the speed check failed)"
            )
        outcome = self.outcome_name()
        if outcome not in (None, "B_OUTCOME_RAN"):
            return self._fail(f"RUN ended the battle as {outcome}, not an escape")
        self.last_action_detail = "RUN"
        return self._ok()

    def await_action_menu(self, max_frames=TURN_FRAMES) -> bool:
        """Advance battle text until the action menu is interactive.

        The turn's opening text ("Wild POOCHYENA appeared!", damage messages,
        stat-change messages) blocks the menu, and it only advances on A. A
        single action verb called from outside play() would otherwise refuse
        forever on a perfectly live battle.

        Pressing A here is safe in a way it is NOT in the overworld: every
        battle screen that asks a question -- the move picker, the party
        list, the bag, the learn prompt -- is detected by name first and
        returns instead of being mashed through.
        """
        spent = 0
        while spent < max_frames and self.active():
            if self.at_action_menu():
                return True
            here = self.menu_name()
            if here in ("moves", "party", "party_popup", "bag", "learn"):
                return self._fail(
                    f"a {here} screen is open; close or answer it before acting"
                )
            self.emu.run_sequence(jitter.sequence(TAP.format("A")))
            spent += 24
        return self.at_action_menu()

    def _back_out(self, presses=4):
        for _ in range(presses):
            if self.at_action_menu():
                return True
            self.emu.run_sequence(jitter.sequence(TAP.format("B")))
        return self.at_action_menu()

    # ---- the level-up move prompt ---------------------------------------------------

    def learn_prompt(self) -> dict | None:
        """The pending "wants to learn X" decision, or None.

        ``gMoveToLearn`` and ``gBattleStruct->expGetterMonId`` name the move
        and the mon; the current moveset comes from the party entry, not from
        ``gBattleMons``, because the mon levelling up is often on the BENCH.
        """
        if not self.at_learn_prompt():
            return None
        move_id = self.emu.u16("gMoveToLearn")
        if not move_id:
            return None
        index = self.emu.u8(
            ("gSharedMem", self.battle_struct["expGetterMonId"])
        )
        party = self.state.party()
        if index >= len(party):
            return None
        mon = party[index]
        md = self.names.move_data(move_id)
        return {
            "party_index": index,
            "nickname": mon.nickname or self.names.species(mon.species),
            "new_move": {
                "id": move_id, "name": md.name, "power": md.power,
                "type": self.names.type(md.type), "pp": md.pp,
                "accuracy": md.accuracy,
            },
            "current": [
                {
                    "slot": s,
                    "id": m,
                    "name": self.names.move(m),
                    "power": self.names.move_data(m).power,
                    "type": self.names.type(self.names.move_data(m).type),
                    "hm": m in self.hm_moves,
                }
                for s, m in enumerate(mon.moves) if m
            ],
        }

    #: Multi-hit effects and how many hits they average, from the engine's own
    #: constants (include/constants/battle_move_effects.h). Base power alone
    #: rates DOUBLE KICK at 30 when it lands twice, and BULLET SEED at 10 when
    #: it lands two to five times -- so a base-power ranking cheerfully throws
    #: both away for a 35-power single hit.
    MULTI_HIT_EFFECTS = {
        29: 3.0,    # EFFECT_MULTI_HIT      -- 2-5 hits, averages ~3
        44: 2.0,    # EFFECT_DOUBLE_HIT
        77: 2.0,    # EFFECT_TWINEEDLE
        104: 2.0,   # EFFECT_TRIPLE_KICK    -- 3 hits, but escalating accuracy
    }

    def move_value(self, move, owner_types=()) -> float:
        """What a move is actually worth to THIS mon, not its base power.

        Three corrections, all from data already in the prompt or the ROM:
        multi-hit moves land more than once, same-type moves get the 1.5x the
        damage formula gives them, and a move that misses does nothing. Status
        moves stay at zero, which is what the keep/replace rules key on.
        """
        power = float(move.get("power") or 0)
        if not power:
            return 0.0
        effect = move.get("effect")
        if effect is None:
            try:
                effect = self.names.move_data(move["id"]).effect
            except Exception:  # noqa: BLE001
                effect = None
        power *= self.MULTI_HIT_EFFECTS.get(effect, 1.0)
        if move.get("type") and move["type"] in owner_types:
            power *= 1.5
        accuracy = move.get("accuracy")
        if accuracy is None:
            try:
                accuracy = self.names.move_data(move["id"]).accuracy
            except Exception:  # noqa: BLE001
                accuracy = 100
        power *= max(1, min(100, int(accuracy or 100))) / 100.0
        return power

    def _learner_types(self, prompt) -> tuple:
        """The learning mon's own types, for the STAB term."""
        try:
            mon = self.state.party()[prompt["party_index"]]
            base = self.names.base_stats(mon.species)
            return tuple(
                self.names.type(t) for t in dict.fromkeys(
                    (base.type1, base.type2)
                )
            )
        except Exception:  # noqa: BLE001
            return ()

    def default_learn(self, prompt) -> int | None:
        """Which slot to overwrite, or None to decline.

        Two rules, both from real losses in the predecessor project:

        * **Never forget an HM move.** ``teach_hm`` there ate a party member's
          only Surf and stranded the run (its journal #28).
        * **Never trade a damaging move for a status move.** A moveset that
          drifts to all-status is how a mon ends up Struggling to death.

        Beyond that: overwrite the weakest move the new one strictly beats,
        preferring a status move that the new damaging move replaces outright.
        """
        new = prompt["new_move"]
        candidates = [m for m in prompt["current"] if not m["hm"]]
        if not candidates:
            return None
        damaging = [m for m in prompt["current"] if m["power"]]
        mine = self._learner_types(prompt)
        value = {m["slot"]: self.move_value(m, mine) for m in prompt["current"]}
        new_value = self.move_value(new, mine)
        if not new["power"]:
            # A status move may only replace another status move, and only
            # when there is at least one damaging move left standing.
            status = [m for m in candidates if not m["power"]]
            if not status or not damaging:
                return None
            return min(status, key=lambda m: (m["power"], m["slot"]))["slot"]
        status = [m for m in candidates if not m["power"]]
        if status and len(damaging) >= 1:
            return min(status, key=lambda m: m["slot"])["slot"]
        weakest = min(candidates, key=lambda m: (value[m["slot"]], m["slot"]))
        if value[weakest["slot"]] >= new_value:
            return None
        return weakest["slot"]

    def naming_open(self) -> bool:
        """True while the nickname keyboard owns input."""
        from .naming import NamingScreen

        try:
            return NamingScreen(self.emu, self.state).is_open()
        except Exception:  # noqa: BLE001 - never lose a battle to a probe
            return False

    def handle_nickname(self, on_nickname=None) -> str:
        """Answer the nickname keyboard deliberately.

        ``on_nickname(species)`` returns the name to type, or None to take the
        species name. Declining is not an option once the keyboard is up -- it
        is already past the YES/NO box -- so the fallback is the species name,
        which is exactly what declining would have produced.
        """
        from .naming import NamingScreen

        kb = NamingScreen(self.emu, self.state)

        # The mon being named is the one just CAUGHT, and it is not in the
        # party yet when the keyboard opens -- reading party[-1] here named a
        # wild LOTAD "COMBUSKEN" after the lead. gBattleMons[1] still holds the
        # capture, so ask it and only fall back to the party.
        species = "?"
        try:
            species = self.battler(1).name
        except Exception:  # noqa: BLE001
            try:
                party = self.state.party()
                if party:
                    species = self.names.species(party[-1].species)
            except Exception:  # noqa: BLE001
                pass

        name = None
        if on_nickname is not None:
            try:
                name = on_nickname(species)
            except Exception as exc:  # noqa: BLE001 - a namer must not kill the fight
                log.warning("[battle] nickname hook raised %s: %s",
                            type(exc).__name__, exc)
        if not name:
            # DECLINING MEANS ACCEPTING, not retyping. The engine has already
            # filled the buffer with the species name, so pressing accept is
            # instant and cannot fail -- whereas typing it walks a keyboard
            # cursor that this ROM will not always move, and every failure
            # re-offers the prompt. Twenty minutes of an underwater sweep went
            # into "named the catch 'CHINCHOU'" over and over.
            typed = kb.accept()
            self._wait(lambda: not self.naming_open(),
                       timeout_frames=SCREEN_FRAMES)
            log.info("[battle] accepted the default name %r", typed)
            return typed
        try:
            typed = kb.type(str(name)[:10])
        except Exception as exc:  # noqa: BLE001
            # A keyboard we cannot drive must not cost the battle or the
            # catch. Confirm whatever is in the buffer -- empty means the
            # species name, which is what declining would have given.
            log.warning("[battle] could not type %r (%s); accepting the "
                        "default name", name, exc)
            typed = kb.accept()
        # Wait for the keyboard to actually go away. Without this the loop
        # comes straight back round, sees it still up and names the same mon
        # twice -- harmless but noisy, and the second pass runs the fallback.
        self._wait(lambda: not self.naming_open(), timeout_frames=SCREEN_FRAMES)
        log.info("[battle] named the catch %r", typed)
        return typed

    def _summary_move_cursor(self) -> int:
        return self.emu.u8(self.pss_base + self.summary_move_index)

    def _summary_open(self) -> bool:
        """``ShowSelectMovePokemonSummaryScreen`` has taken the screen when the
        main callback is no longer the battle's."""
        return self.state.callback_name() != "BattleMainCB2"

    def handle_learn(self, on_learn=None) -> bool:
        """Answer the pending move-learn prompt. Returns True when it was
        answered (either way), False when there was nothing to answer."""
        prompt = self.learn_prompt()
        if prompt is None:
            return False
        choice = None
        source = "default"
        if on_learn is not None:
            choice = on_learn(dict(prompt))
            source = "hook"
            if choice is None:
                choice, source = self.default_learn(prompt), "default (hook had no opinion)"
        else:
            choice = self.default_learn(prompt)
        if choice is False:
            choice = None
            source = "hook declined"

        new = prompt["new_move"]
        if choice is None:
            log.info(
                "[battle] harness declined %s for %s (%s): keeping %s",
                new["name"], prompt["nickname"], source,
                "/".join(m["name"] for m in prompt["current"]),
            )
            self._decline_learn()
            self._learns.append({**prompt, "forgot": None, "source": source})
            return True

        forgotten = next(
            (m for m in prompt["current"] if m["slot"] == choice), None
        )
        if forgotten is None:
            log.warning(
                "[battle] move-learn choice %r is not one of %s's slots; "
                "declining instead", choice,
                prompt["nickname"],
            )
            self._decline_learn()
            self._learns.append({**prompt, "forgot": None, "source": "invalid choice"})
            return True
        if forgotten["hm"]:
            log.warning(
                "[battle] refusing to forget the HM move %s for %s (%s)",
                forgotten["name"], new["name"], source,
            )
            self._decline_learn()
            self._learns.append({**prompt, "forgot": None, "source": "HM guard"})
            return True

        log.info(
            "[battle] harness chose to forget %s (slot %d) for %s on %s (%s)",
            forgotten["name"], choice, new["name"], prompt["nickname"], source,
        )
        self._answer_yes_no(True)
        if not self._wait(self._summary_open):
            self._learns.append({**prompt, "forgot": None, "source": "summary never opened"})
            return self._fail(
                "answered YES to the move prompt but the move-selection "
                "summary screen never opened"
            )
        if not self._drive_summary_cursor(choice):
            self._learns.append({**prompt, "forgot": None, "source": "cursor stuck"})
            return False
        self.emu.run_sequence(jitter.sequence(TAP.format("A")))
        self._wait(lambda: not self._summary_open(), timeout_frames=SCREEN_FRAMES)
        self._learns.append({**prompt, "forgot": forgotten["name"], "source": source})
        return True

    def _decline_learn(self) -> None:
        """Say NO to the learn prompt AND YES to the confirmation behind it.

        Gen 3 asks twice: "Delete an older move?" -> NO, then "Give up on
        learning <MOVE>?" -> YES. Answering only the first leaves the second
        box up, and the engine walks straight back to the first one -- an
        endless "harness declined THIEF for MIGHTYENA" loop that ate every
        battle in a grind while the party gained nothing. Same shape as the
        re-arming menus in AGENTS.md gotcha 18.
        """
        self._answer_yes_no(False)
        for _ in range(6):
            if self.learn_prompt() is None:
                return
            # The follow-up box wants YES to abandon the move.
            self._answer_yes_no(True)
            self.emu.run_sequence(".:12")

    def _answer_yes_no(self, yes) -> bool:
        """``gBattleCommunication[1]`` is the YES/NO cursor for the move
        prompt (0 = YES, 1 = NO; battle_script_commands.c:5296-5310)."""
        want = 0 if yes else 1
        for _ in range(4):
            current = self.emu.u8(("gBattleCommunication", 1))
            if current == want:
                self.emu.run_sequence(jitter.sequence(TAP.format("A")))
                return True
            self.emu.run_sequence(TAP.format("DOWN" if want else "UP"))
            if self.emu.u8(("gBattleCommunication", 1)) == current:
                break
        # The cursor refused to move; B is NO no matter where it sits.
        if not yes:
            self.emu.run_sequence(jitter.sequence(TAP.format("B")))
            return True
        return self._fail(
            "could not move the YES/NO cursor to YES "
            f"(gBattleCommunication[1] = {self.emu.u8(('gBattleCommunication', 1))})"
        )

    def _drive_summary_cursor(self, slot, max_steps=10) -> bool:
        for _ in range(max_steps):
            current = self._summary_move_cursor()
            if current == slot:
                return True
            key = "DOWN" if slot > current else "UP"
            self.emu.run_sequence(jitter.sequence(TAP.format(key)))
            if self._summary_move_cursor() == current:
                return self._fail(
                    f"summary move cursor stuck on {current} (wanted {slot})"
                )
        return self._fail(
            f"summary move cursor never reached {slot} "
            f"(still {self._summary_move_cursor()})"
        )

    # ---- the loop ---------------------------------------------------------------------

    def _play_safari(self, policy, start, max_frames, on_nickname) -> dict:
        """Play a Safari battle: BALL / GO NEAR / RUN, no moves involved.

        The policy is asked with the same shape as anywhere else and may
        return ``('ball', name)``, ``'go_near'`` or ``'flee'``. With nothing
        steering it the answer is a ball, because a Safari battle exists only
        to catch: there is no damage to deal and fleeing forfeits the
        encounter. GO NEAR is not taken by default -- it buys +4 catch factor
        but pays +4 flee rate (pret/data/btl_attrs.s:387-391), so it is a
        judgement call, not a free win.
        """
        thrown = 0
        while self.active():
            if self.emu.frame - start > max_frames:
                return self._result("timeout", start, "frame budget exhausted")
            if self.naming_open():
                self.handle_nickname(on_nickname)
                continue
            if not self.at_safari_menu():
                # Ball animations, "It broke free!", the dex entry: text, not
                # a decision. drain_scene presses nothing, which is the whole
                # point -- an A here is another ball.
                self.emu.tick(jitter.frames(20))
                continue
            action = None
            if policy is not None:
                try:
                    action = policy(self.frame())
                except Exception as exc:            # noqa: BLE001
                    log.warning("[safari] policy raised %s: %s",
                                type(exc).__name__, exc)
            if action is None:
                action = ("ball", None)
            kind = action[0] if isinstance(action, tuple) else action
            if kind == "flee":
                self.safari_flee()
                continue
            if kind in ("go_near", "near"):
                self.safari_go_near()
                continue
            # There is only ONE ball in a Safari encounter -- the BALL option
            # IS the throw (src/battle_controller_safari.c:207-228), so a
            # ('ball', NAME) from a generic policy names nothing to choose.
            if not self.safari_ball():
                return self._result("stuck", start, self.last_reason
                                    or "the safari ball would not throw")
            thrown += 1
            log.info("[safari] ball %d thrown", thrown)
        return self._result(self.outcome_name(), start,
                            f"safari battle ended after {thrown} ball(s)")

    def play(self, policy=None, max_frames=200_000, on_learn=None,
             on_nickname=None) -> dict:
        """Fight the battle to its end.

        ``policy(frame)`` may return ``("attack", slot)``, ``("switch", index)``,
        ``("item", name)``, ``("ball", name)``, ``"flee"`` or ``None``. ``None``
        hands the turn to :meth:`pokeagent.tactics.Tactics.recommend`, and
        **every harness-made choice is logged with its reason**: a decision
        nobody can audit is exactly how the predecessor fought whole battles
        with GROWL.

        A policy that RAISES is not the same as a policy that declined -- the
        predecessor conflated the two and silently fell back to move slot 0,
        which for most movesets is GROWL (its journal #21). Here the exception
        is logged with a traceback, recorded on the turn, and only THEN does
        tactics take over.
        """
        start = self.emu.frame
        self.turns = []
        self._turn_no = 0
        # Actions this battle has PROVEN do nothing. Reset per battle: a move
        # the engine refuses here (disabled, or simply never executed) may be
        # perfectly good in the next fight.
        self._dead_actions: set[str] = set()
        #: Set when a switch is refused. The party menu is the one control
        #: surface that can wedge a whole battle, so it gets one chance.
        self._switch_broken = False
        self._learns = []
        stall = 0
        last_vitals = None
        last_failed = None
        failures = 0

        while self.active():
            if self.emu.frame - start > max_frames:
                return self._result("timeout", start, "frame budget exhausted")

            if self.at_safari_menu():
                # A SAFARI BATTLE HAS NO MOVES AND NO PARTY: the engine zeroes
                # the player's side (pret/src/battle_main.c:3711-3715) and the
                # menu is BALL / POKEBLOCK / GO NEAR / RUN. Everything below
                # this point needs tactics.outlook() and a move slot, so the
                # loop used to spin with the frame counter climbing and the
                # player frozen -- a peer measured a watchdog reporting the
                # same cell pinned for 840s, then 730s, then 640s, with one
                # 18-minute trip spending 9 of its 424 steps and never moving
                # the dex. Drive the four options instead.
                return self._play_safari(policy, start, max_frames, on_nickname)

            if self.at_learn_prompt():
                self.handle_learn(on_learn)
                continue

            if self.naming_open():
                # A CATCH ends with the nickname keyboard, and the generic
                # "not at the action menu -> press A" branch below types into
                # it: a caught LOTAD came out named AAAAAAAAAA, which is this
                # project's own stray-A naming bug reproduced exactly. A name
                # is a decision, so it is asked for or declined, never mashed.
                self.handle_nickname(on_nickname)
                continue

            if self.at_move_menu():
                # The move picker is open without us having opened it (a
                # cancelled selection). A here would re-pick whatever the
                # cursor happens to sit on, so back out to a known screen.
                self.emu.run_sequence(jitter.sequence(TAP.format("B")))
                continue

            if self.at_party_menu() and not self.at_action_menu():
                # Only a REPLACEMENT is driven by the forced path. The engine
                # says which it is: gUnknown_02038473 == 1 means "send this one
                # out directly, no SHIFT popup"
                # (src/battle_party_menu.c:446). A voluntary switch that got
                # interrupted leaves the same menu on screen with the flag at
                # 0, and driving it as a forced one selects a slot the engine
                # never applies -- "sent out party slot 0 but
                # gBattlerPartyIndexes[0] is still 1". The battle then returned
                # "stuck" every time the loop re-entered it: measured, four
                # battles in a row against the same ZIGZAGOON, forever.
                forced = self.emu.u8("gUnknown_02038473") == 1
                if not forced and getattr(self, "_stale_party", 0) >= 2:
                    # B DID NOT DISMISS IT, SO IT WAS NEVER STALE. The flag
                    # is not set for every replacement the engine demands, and
                    # when a mon faints mid-turn the menu that comes up cannot
                    # be backed out of -- so this branch pressed B against an
                    # immovable menu while the opponent attacked for free.
                    # Measured in the Elite Four: ten consecutive "stale party
                    # menu" lines, then "sent out party slot 1 but
                    # gBattlerPartyIndexes is [0]", and the run lost to Drake
                    # twice with items still in the bag.
                    #
                    # Trusting the observation over the flag: a menu that
                    # survives two B presses is a replacement, whatever the
                    # flag says.
                    log.info("[battle] party menu survived %d B presses -- "
                             "treating it as a forced replacement",
                             self._stale_party)
                    forced = True
                if forced:
                    self._stale_party = 0
                    if not self._forced_switch():
                        return self._result("stuck", start, self.last_reason)
                else:
                    self._stale_party = getattr(self, "_stale_party", 0) + 1
                    log.info("[battle] stale party menu (not a forced "
                             "replacement); backing out")
                    self.emu.run_sequence(jitter.sequence(TAP.format("B")))
                continue
            self._stale_party = 0

            if not self.at_action_menu():
                if self.at_move_menu():
                    # THE PROTECT BUG. This blind A exists to page through
                    # battle text, and the move picker is not text -- it is a
                    # SELECTION, and A here sends whatever the cursor happens
                    # to be sitting on. That is slot 0, and slot 0 was PROTECT
                    # with no PP, so the engine answered "no PP left" and the
                    # loop did it again next turn, and the turn after.
                    #
                    # Reported three times from watching the screen while
                    # every guard I added was in `attack()` -- which was never
                    # reached, because nothing here asked it. The ledger stops
                    # a chosen empty move; only this stops an unchosen one.
                    #
                    # Same shape as the mart's re-arming YES/NO box and Bill's
                    # PC list: a blind A loop over a menu that re-arms itself
                    # is a repeat-action loop.
                    self.emu.run_sequence(jitter.sequence(TAP.format("B")))
                    continue
                self.emu.run_sequence(jitter.sequence("A:2 .:10"))
                continue

            analysis = self.tactics.outlook()
            if analysis is None:
                # gBattleMons is not ready; do not invent a matchup.
                self.emu.tick(8)
                continue

            frame = self.frame()
            action, why, source = self._decide(policy, frame, analysis)
            self._turn_no += 1
            turn = Turn(
                number=self._turn_no,
                action=action,
                detail="",
                why=why,
                my_mon=analysis["me"].nickname or analysis["me"].name,
                their_mon=analysis["enemy"].name,
                my_hp_before=analysis["me"].hp,
                their_hp_before=analysis["enemy"].hp,
            )
            self.last_action_detail = ""
            # Read BEFORE the action: a thrown ball is spent whether or not
            # it catches, and that is the only evidence a throw happened.
            balls_before = self._ball_count()
            ok = self._execute(action)
            turn.ok = ok
            turn.detail = self.last_action_detail or str(action)
            if not ok:
                turn.note = self.last_reason or "action failed"
                self._back_out()
            self._settle()
            after_me, after_enemy = self._vitals()
            turn.my_hp_after = after_me
            turn.their_hp_after = after_enemy
            turn.note = (turn.note + "; " if turn.note else "") + f"chosen by {source}"
            self.turns.append(turn)
            log.info("[battle] %s", turn)

            # An action the engine refuses is not a stalemate to wait out --
            # it is a decision that cannot be executed. The training policy
            # asked to switch to a party slot the engine would not send out
            # ("confirmed SHIFT to slot 0 but gBattlerPartyIndexes[0] is still
            # 1"), got False, was asked again, and answered the same thing
            # forever: 22 battles against the same ZIGZAGOON without a step.
            # After two identical failures the policy is DROPPED for the rest
            # of this battle and tactics takes over, loudly.
            if not turn.ok and action == last_failed:
                failures += 1
                if failures >= 2 and policy is not None:
                    log.warning(
                        "[battle] policy keeps asking for %r and the engine "
                        "keeps refusing it (%s); ignoring the policy for the "
                        "rest of this battle", action, turn.note,
                    )
                    policy = None
                    failures = 0
            else:
                failures = 0 if turn.ok else 1
            last_failed = None if turn.ok else action
            if not turn.ok:
                # A REFUSAL is definitive and needs no second opinion: the
                # engine has already said no. Waiting for two turns of
                # unchanged HP before retiring it is a rule for moves that
                # execute and accomplish nothing, and it left a Pelipper
                # choosing PROTECT with 0 PP over and over because tactics
                # ranks off an analysis whose PP had gone stale. Whatever the
                # analysis believes, the engine's answer wins.
                self._dead_actions.add(repr(action))
                log.warning(
                    "[battle] the engine refused %r (%s) -- retiring it for "
                    "this battle", action, turn.note,
                )

            # A BALL IS NOT MEASURED IN HP. It never moves either bar by
            # design, so the HP-stall rule called every single throw futile:
            # two throws retired the ball action, four FLED the battle, and a
            # collection run flew to three maps and caught nothing while
            # reporting six consecutive "changed neither side's HP" lines
            # against one full-health MEDICHAM.
            #
            # The right measure for a throw is whether it was actually
            # THROWN, and the bag says so. A consumed ball is progress: the
            # attempt happened and failed, which is a dice roll, not a stuck
            # action -- and it is self-limiting, because the supply runs out.
            # An unreadable count (-1) counts as spent: a failed bag read is
            # not evidence the throw did not happen, and `fight`'s frame cap
            # still bounds the battle either way.
            spent_a_ball = self._is_ball(action) and (
                balls_before < 0 or self._ball_count() < balls_before
            )
            if (after_me, after_enemy) == last_vitals and not spent_a_ball:
                stall += 1
                # Returning "stalled" told the truth and changed nothing: the
                # caller re-entered the same battle and picked the same move,
                # so a Lottad whose STRENGTH the engine would not execute sat
                # at 67 HP against a Grimer at 21 for hundreds of turns, the
                # turn counter cycling T1..T5 forever. An action that provably
                # changes nothing is retired, and once nothing is left the
                # battle is ESCAPED rather than re-entered.
                if stall >= 2:
                    self._dead_actions.add(repr(action))
                    key = self._futile_key(action)
                    if key is not None:
                        self._futile.add(key)
                    log.warning(
                        "[battle] %r changed neither side's HP twice -- "
                        "retiring it for this battle (dead: %s)",
                        action, sorted(self._dead_actions),
                    )
                if stall >= 4:
                    # `self.wild()` was called here and HAS NEVER EXISTED, so
                    # the one path that escapes a stalled battle crashed the
                    # whole process with `'BattleSession' object has no
                    # attribute 'wild'` -- it killed a five-hour collection run
                    # on Route 121. `can_flee` is the real predicate (a port of
                    # `CanRunFromBattle`) and it answers the trainer case
                    # directly, which is all `wild()` was standing in for.
                    if self.can_flee()[0] and self.flee():
                        return self._result(
                            "fled", start,
                            f"nothing on this turn moved either HP bar "
                            f"(retired {sorted(self._dead_actions)})",
                        )
                    return self._result(
                        "stalled", start,
                        f"four consecutive turns changed neither side's HP "
                        f"(last {action!r}: {turn.note})",
                    )
            else:
                stall = 0
            last_vitals = (after_me, after_enemy)

        return self._result(self.outcome_name() or "ended", start, None)

    def _decide(self, policy, frame, analysis):
        """``(action, why, source)``. A crashing policy is NOT a declining one."""
        if policy is not None:
            try:
                action = policy(frame)
            except Exception as exc:  # noqa: BLE001 - a policy must not kill the fight
                log.error(
                    "[battle] policy raised %s: %s -- falling through to "
                    "tactics for this turn", type(exc).__name__, exc,
                    exc_info=True,
                )
                action = None
                policy_error = f"{type(exc).__name__}: {exc}"
            else:
                policy_error = None
            if action is not None:
                return action, f"policy returned {action!r}", "policy"
            reason_tail = (
                f" (policy raised {policy_error})" if policy_error
                else " (policy declined)"
            )
        else:
            reason_tail = ""
        action, why = self.tactics.recommend(analysis)
        if repr(action) in getattr(self, "_dead_actions", ()):
            alt = self._live_alternative(analysis)
            log.warning(
                "[battle] tactics wants retired %r; using %r instead",
                action, alt,
            )
            action, why = alt, f"{action!r} was retired this battle"
        log.info("[battle] harness chose %r: %s%s", action, why, reason_tail)
        # The SOURCE carries the tail too. `why` is the long explanation but
        # `note` is what the one-line audit log prints, and "the policy
        # crashed" must be visible there -- conflating a crashing policy with
        # a declining one is how the predecessor silently fell back to slot 0
        # and picked a status move (its journal #21).
        return action, why + reason_tail, "tactics" + reason_tail

    @staticmethod
    def _is_ball(action) -> bool:
        """Is this action a ball throw? ``('ball', 'ULTRA BALL')``."""
        return isinstance(action, tuple) and len(action) > 1 and action[0] == "ball"

    def _ball_count(self) -> int:
        """Total balls in the bag.

        The sum is enough: which ball was thrown does not matter, only that
        one left the bag. Never raises -- a failed bag read must not decide a
        battle, so it reports -1, which can only make a throw look spent and
        so errs toward letting the catch continue.
        """
        try:
            return sum((self.state.bag().get("poke_balls") or {}).values())
        except Exception:  # noqa: BLE001 - a diagnostic must not end a battle
            return -1

    def _futile_key(self, action):
        """Identity of a useless ACTION that outlives the battle.

        Keyed on the species and the move, so the lesson follows the mon
        rather than a battler index. Only attacks earn an entry: a switch or
        an item that did nothing says something about this turn, not about the
        move forever.
        """
        if not (isinstance(action, tuple) and action and action[0] == "attack"):
            return None
        try:
            me = self.battler(self.menu_battler())
        except Exception:  # noqa: BLE001 - unreadable battler: no lesson
            return None
        if me is None:
            return None
        slot = action[1]
        moves = getattr(me, "moves", ()) or ()
        if not isinstance(slot, int) or slot >= len(moves) or not moves[slot]:
            return None
        return (getattr(me, "species", 0), moves[slot])

    def futile(self, action) -> bool:
        """Has this exact move already proven to change nothing, EVER?

        Retirement used to last one battle, and that is why a run burned ten
        PROTECTs: each battle started with a clean slate, picked PROTECT once,
        watched it change nothing, retired it, and forgot. Ten battles later
        the move was at 0 PP and the mon was a zombie whose only remaining
        options were status moves. The count of uses was the giveaway --
        PROTECT at 0/10 while every other move sat at maximum.

        So the lesson persists for the whole run. It is not a ban: a move here
        is merely LAST, and `_live_alternative` will still reach for it when
        there is genuinely nothing else.
        """
        key = self._futile_key(action)
        return key is not None and key in self._futile

    def _live_alternative(self, analysis):
        """The best action that has not already proven useless this battle.

        Ordered by what keeps a fight moving: a usable damaging move first,
        then a switch, then flight. Returning the retired action again is the
        one thing this must never do -- that is the loop it exists to break.
        """
        dead = getattr(self, "_dead_actions", set())
        def field(row, name, default=0):
            if isinstance(row, dict):
                return row.get(name, default)
            return getattr(row, name, default)

        moves = [
            m for m in (analysis or {}).get("moves", ())
            if field(m, "pp") and field(m, "kind") != "empty"
            and repr(("attack", field(m, "slot"))) not in dead
        ]
        # Proven-useless moves sort LAST, not out: if the only thing left is
        # a move that did nothing before, it is still better than standing
        # there. Damage first within each group.
        moves.sort(key=lambda m: (
            self.futile(("attack", field(m, "slot"))),
            -(field(m, "damage_max") or field(m, "power") or 0),
        ))
        if getattr(self, "_switch_broken", False) and not moves \
                and repr("flee") not in dead:
            # Nothing to attack with and switching is broken: flee rather than
            # wedge the party menu again -- but only while flight is still a
            # live option. In a trainer battle it never is, and returning it
            # from here regardless was the earlier of the two unguarded exits
            # that deadlocked the Elite Four.
            return "flee"
        if moves:
            return ("attack", field(moves[0], "slot"))
        # Whoever is ALREADY standing there is not a replacement. The old test
        # was `if idx`, which only excluded slot 0 -- so on Route 121, with a
        # fainted LOTTAD in slot 0 and MIGHTYENA active in slot 1, it proposed
        # ("switch", 1) over and over. The engine answered "party slot 1 is
        # already the active battler", the action got retired, flight was
        # refused because the Kecleon ambush is a trainer battle, and the run
        # deadlocked for twelve minutes against a foe on 13 HP with four
        # healthy mons on the bench.
        active = (analysis or {}).get("active_party_index")
        for idx, mon in enumerate(
            () if getattr(self, "_switch_broken", False)
            else (analysis or {}).get("party", ())
        ):
            # `party` carries Mon OBJECTS, not dicts -- assuming otherwise
            # crashed the whole run mid-battle on Route 118 the first time a
            # retired action needed a replacement. Read it either way.
            hp = mon.get("hp") if isinstance(mon, dict) else getattr(
                mon, "hp", 0)
            # Known active index wins; when the analysis does not carry one,
            # fall back to the old proxy of skipping slot 0, which is usually
            # the mon on the field. Dropping the proxy would let an unknown
            # active mon be proposed as its own replacement.
            if not hp or (idx == active if active is not None else idx == 0):
                continue
            if repr(("switch", idx)) not in dead:
                return ("switch", idx)
        # An ITEM before flight. Running away ends the battle with nothing
        # gained; a potion may make the next turn winnable, and the bag is
        # there to be spent. Only worth trying when the active mon is
        # actually hurt -- healing at full HP is the engine refusing an item
        # and another dead turn.
        try:
            heal = self.tactics._cheapest_heal(analysis)
        except Exception:  # noqa: BLE001 - no bag, no item option
            heal = None
        if heal and repr(("item", heal[0])) not in dead:
            return ("item", heal[0])
        if repr("flee") not in dead:
            return "flee"
        # STRUGGLE IS THE FLOOR. Every other option is dead: no move has PP,
        # no legal switch remains, the bag has nothing, and flight is refused
        # -- which it always is in a trainer battle. Returning "flee" here
        # anyway broke this method's one promise ("returning the retired
        # action again is the one thing this must never do") and deadlocked
        # the Elite Four: tactics wanted ("switch", 0), it was retired, the
        # fallback was flee, flee was refused as a trainer battle and retired,
        # and then the same two were offered again forever. Reported from the
        # couch as "stuck flipping through the pokemon selection menu".
        #
        # An attack with no PP is exactly what the engine turns into Struggle,
        # so this always resolves the turn: damage happens, the stalemate
        # ends, and the battle reaches a real conclusion either way.
        log.info("[battle] every action is retired -- attacking anyway so the "
                 "engine Struggles and the turn resolves")
        return ("attack", 0)

    def _execute(self, action) -> bool:
        kind = action[0] if isinstance(action, tuple) else action
        arg = action[1] if isinstance(action, tuple) and len(action) > 1 else None
        if kind == "attack":
            return self.attack(arg)
        if kind == "switch":
            ok = self.switch_to(arg if arg is not None else 1)
            if not ok:
                # ONE FAILED SWITCH IS ENOUGH. A switch that does not take
                # leaves the engine in `sub_802DF88`, the party-menu RETURN
                # handler (battle_controller_player.c:1521-1532), which waits
                # on `gMain.callback2 == BattleMainCB2 && !gPaletteFade.active`
                # -- and if the party screen was left half-driven it waits
                # forever. The action menu never comes back, every later
                # action is refused with "the action menu is still up", and the
                # battle ends "stuck".
                #
                # That is what stood between the run and badge 6 for a day:
                # Winona's fight wedged exactly here, while the same fight with
                # an attack-only policy was won in 28 turns. So after a refused
                # switch, stop offering switches for the rest of this battle
                # and let the damage maths play it out.
                self._switch_broken = True
                log.warning(
                    "[battle] a switch was refused (%s) -- no more switching "
                    "this battle", self.last_reason,
                )
            return ok
        if kind == "item":
            return self.use_item(arg)
        if kind == "ball":
            return self.throw_ball(arg)
        if kind == "flee":
            return self.flee()
        if kind == "go_near":
            return self.safari_go_near()
        return self._fail(f"unknown action {action!r}")

    def _settle(self):
        """Let the turn resolve back to a menu, the party screen or the end."""
        self._wait(
            lambda: self.at_action_menu()
            or self.at_party_menu()
            or self.at_learn_prompt()
            or not self.active(),
            timeout_frames=TURN_FRAMES,
            press="A:2 .:10",
        )

    def _vitals(self):
        """``(my hp, their hp)`` for the turn log.

        Deliberately NOT gated on :meth:`active`. ``gBattleMons`` survives the
        end of the battle intact -- verified live: after a won fight it still
        reads MUDKIP 17/21 and POOCHYENA 0/13 -- so gating this on "am I still
        in a battle" wrote ``me 17->0`` into the last turn of a battle we won
        and put a phantom self-KO in front of anyone auditing the log.

        Only once the engine actually clears the blocks does this fall back:
        my side to the live party entry, their side to a plain zero, which by
        then is the only thing that could have ended the fight.
        """
        me, enemy = self.battler(0), self.battler(1)
        my_hp = me.hp if me.max_hp else None
        if my_hp is None:
            party = self.state.party()
            index = self.party_index_of(0)
            my_hp = party[index].hp if index < len(party) else 0
        return (my_hp, enemy.hp if enemy.max_hp else 0)

    def _forced_switch(self) -> bool:
        """The engine opened the party menu itself: our mon fainted.

        Pick the best answer to what is standing there rather than the first
        living slot -- the successor walks into a free hit either way, so it
        may as well be one that resists.
        """
        analysis = self.tactics.outlook()
        options = self.tactics.switch_options(analysis) if analysis else []
        if not options:
            legal = self.switchable()
            if not legal:
                return self._fail(
                    "the engine wants a replacement and every other party "
                    "member is fainted, an egg or missing"
                )
            choice = legal[0]
            why = "first living party member (no analysis available)"
        else:
            choice = options[0]["index"]
            why = (
                f"{options[0]['nickname']} takes "
                f"x{options[0]['incoming_mult']:g} of the incoming move at "
                f"{options[0]['hp_frac']:.0%} HP"
            )
        log.info("[battle] harness chose replacement slot %d: %s", choice, why)
        if not self._drive_party_cursor(choice):
            return False
        self.emu.run_sequence(jitter.sequence(TAP.format("A")))
        # A forced switch-in has no SHIFT popup: the mon is sent out directly
        # when gUnknown_02038473 == 1 (src/battle_party_menu.c:498-499).
        if self.at_party_popup():
            self.emu.run_sequence(jitter.sequence(TAP.format("A")))
        if not self._wait(
            lambda: self.sent_out(choice) or not self.active(),
            timeout_frames=TURN_FRAMES,
            press="A:2 .:10",
        ):
            # THE GOAL IS "SOMEONE IS OUT", NOT "MY PICK IS OUT". The engine
            # does not always honour the slot chosen at a forced replacement,
            # and treating that as a failure abandoned the whole battle:
            # `play()` answers "stuck" on a False here, so a gauntlet that was
            # otherwise going fine was thrown away by a mon being sent out
            # that we had not nominated. Logged repeatedly across Elite Four
            # attempts as "sent out party slot 4 but gBattlerPartyIndexes is
            # [0]" -- and slot 0 was alive and swinging.
            #
            # So check the thing that actually matters: is a living mon
            # standing there? If so the replacement happened and the battle
            # can continue; only a genuinely empty field is a failure.
            live = [i for i in (self.party_index_of(b)
                                for b in self.my_battlers()) if i is not None]
            standing = None
            for idx in live:
                mon = self.party_mon(idx) if hasattr(self, "party_mon") else None
                if mon is None or (getattr(mon, "hp", 0) or 0) > 0:
                    standing = idx
                    break
            if standing is not None:
                log.info("[battle] asked for slot %d, the engine sent slot %d "
                         "-- a living mon is out, so play on", choice, standing)
                choice = standing
            else:
                return self._fail(
                    f"sent out party slot {choice} but gBattlerPartyIndexes is "
                    f"{[self.party_index_of(b) for b in self.my_battlers()]}"
                )
        # gBattleMons[0] is not repopulated with the replacement the instant
        # gBattlerPartyIndexes flips, so snapshot the log row only once the
        # send-out has actually resolved -- otherwise the audit records the
        # incoming mon at 0 HP.
        self._settle()
        after_me, after_enemy = self._vitals()
        self._turn_no += 1
        self.turns.append(
            Turn(
                number=self._turn_no,
                action=("switch", choice),
                detail=f"forced#{choice}",
                why=why,
                my_mon="(fainted)",
                their_mon=analysis["enemy"].name if analysis else "?",
                my_hp_before=0,
                their_hp_before=analysis["enemy"].hp if analysis else 0,
                my_hp_after=after_me,
                their_hp_after=after_enemy,
                note="forced replacement",
            )
        )
        return True

    def _result(self, outcome, start, reason) -> dict:
        return {
            "outcome": outcome,
            "reason": reason,
            "frames": self.emu.frame - start,
            "turns": list(self.turns),
            "free_hits": self.free_hits(),
            "learned": list(self._learns),
            "log": self.summary_text(),
        }

    # ---- the audit ----------------------------------------------------------------------

    def free_hits(self) -> list:
        """Turns that handed the opponent a move for nothing.

        A switch-in, an item turn or a failed action all spend the turn
        without attacking, so the opponent swings unopposed.

        The test is "did I land an attack", NOT "did my HP drop". HP delta
        looked like the obvious metric and is wrong: verified live, a POTION
        turn healed 19 -> 21 and then took a 2 HP TACKLE, netting exactly
        zero and hiding the free hit completely. That is the precise shape of
        the predecessor's three consecutive Super Potions -- the turns this
        metric exists to surface -- so an HP-delta test would have missed the
        very wipe it was written for. ``net_hp_change`` is reported
        alongside, signed, so a masked hit is visible rather than absent.
        """
        out = []
        for t in self.turns:
            if t.kind == "attack" and t.ok:
                continue
            if t.their_hp_after <= 0:
                continue   # the fight ended on this turn: nobody swung back
            out.append({
                "turn": t.number,
                "action": t.action,
                "detail": t.detail,
                "net_hp_change": t.my_hp_after - t.my_hp_before,
                "damage_taken": t.damage_taken,
                "ok": t.ok,
                "note": t.note,
            })
        return out

    def summary_text(self) -> str:
        lines = [str(t) for t in self.turns]
        free = self.free_hits()
        if free:
            listed = ", ".join(
                f"T{f['turn']} {f['detail']} ({f['net_hp_change']:+d} HP)"
                for f in free
            )
            lines.append(
                f"free hits: {len(free)} turn(s) gave the opponent a move "
                f"without attacking -- {listed}"
            )
        return "\n".join(lines)
