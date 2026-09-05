#!/usr/bin/env python
"""Rename a party Pokemon through the Name Rater in Slateport City.

Why this exists: the run carries a CORPHISH literally nicknamed ``A`` -- the
signature of the harness's #1 bug class, a blind A-loop into a naming
keyboard (docs/gen3/AGENTS.md gotcha 5). The catch path is fixed now
(`pokeagent/battle.py:handle_nickname` calls `NamingScreen.accept()`, and
`pret/src/naming_screen.c:681-685` shows START runs `MoveCursorToOKButton`,
so the species name pre-filled by the engine is what gets taken). This
script repairs the one mon that was named before that fix.

Everything below comes out of the cartridge rather than off a wiki.

The NPC
-------
`pret/data/maps/SlateportCity_House1/map.json` has exactly one object_event,
`OBJ_EVENT_GFX_OLD_MAN_1` at **(7, 5)**, script
`SlateportCity_NameRatersHouse_EventScript_NameRater`. The house's two warp
tiles are (3,7) and (4,7), both back to `MAP_SLATEPORT_CITY` warp 6, which
`pret/data/maps/SlateportCity/map.json` places at **(5, 19)**.

The conversation, from `SlateportCity_House1/scripts.inc`
---------------------------------------------------------
    msgbox Text_PleasedToRateMonNickname, MSGBOX_YESNO   <- YES
    -> EventScript_ChooseMonToRate
       msgbox Text_CritiqueWhichMonNickname, MSGBOX_DEFAULT
       special SelectMonForNPCTrade                      <- party picker
       waitstate
       compare VAR_0x8004, 255 / goto_if_ne RateMonNickname
    -> EventScript_RateMonNickname
       ... OT checks ...
       msgbox Text_FineNameSuggestBetterOne, MSGBOX_YESNO <- YES
    -> EventScript_ChangeNickname
       msgbox Text_WhatShallNewNameBe, MSGBOX_DEFAULT
       call Common_EventScript_NameReceivedPartyMon       <- keyboard

Three prompts, each answered deliberately. Nothing here mashes A into a box
whose state was not read first.

Two preconditions the script silently refuses on, so they are checked BEFORE
the trip rather than discovered in Slateport:

* `TV_CheckMonOTIDEqualsPlayerID` (`pret/src/tv.c:2088-2094`) compares the
  mon's full 32-bit `MON_DATA_OT_ID` against `GetPlayerTrainerId()`
  (`tv.c:2110-2113`, all four `playerTrainerId` bytes -- not the 16-bit
  public ID that `state.trainer_id()` returns).
* `MonOTNameMatchesPlayer` (`pret/src/field_specials.c:1900-1907`) compares
  `MON_DATA_OT_NAME` against `gSaveBlock2.playerName`.

A traded mon fails either and the rater just says "magnificent name" and
releases -- a wasted flight with no error.

The party picker
----------------
`SelectMonForNPCTrade` (`pret/src/script_pokemon_util_80F99CC.c:42-49`)
opens `PARTY_MENU_TYPE_IN_GAME_TRADE`, whose input handler is
`HandleSelectPartyMenu` (`pret/src/party_menu.c:448`,
`script_pokemon_util_80F99CC.c:158-177`). That is a DIFFERENT task name from
the two the harness already reads (`HandleBattlePartyMenu`,
`HandleDefaultPartyMenu`), which is why `battle._party_cursor()` returns
None here and the slot is read locally instead. The expression is the
engine's own: `sub_806CA38(taskId)` is
``gSprites[gTasks[taskId].data[3] >> 8].data[0]`` (`party_menu.c:1773-1787`),
and `ChangeDefaultPartyMenuSelection` (`party_menu.c:1381+`) walks it as a
flat list 0..count-1 then 7 for CANCEL -- so UP/DOWN converges even though
the screen is drawn as a tall box plus a two-column grid. The drawn layout is
NOT the index (`party_menu.c:268`); the sprite's `data[0]` is.

Belt and braces: `HandleSelectPartyMenu` writes the accepted slot into
`gSpecialVar_0x8004`, so after pressing A the choice is read back from the
variable the script itself branches on.

The keyboard, and the trap in it
--------------------------------
`ChangePokemonNickname` (`pret/src/tv.c:2062-2074`) hands `DoNamingScreen`
`gStringVar2` **pre-loaded with the current nickname**, as both the initial
text and the destination buffer. And `sub_80B74B0` (`naming_screen.c:1577-
1589`) only copies `textBuffer` into `destBuffer` if the buffer holds at
least one real character:

    for (i = 0; i < maxChars; i++)
        if (textBuffer[i] != 0 && textBuffer[i] != 0xFF) { StringCopyN(...); break; }

So on THIS screen, unlike the catch nickname, pressing OK on an empty buffer
is not "take the default" -- it keeps the old name. `NamingScreen.accept()`
is therefore *not* a safe fallback here: it would silently re-confirm ``A``
and report success. The buffer is cleared with B (which is
`DeleteTextCharacter`, `naming_screen.c:541-545`, and does not close the
screen), the name is typed, the buffer is compared to the target, and only
then is OK pressed. If the buffer will not hold the name, the script says so
instead of pressing blind.
"""

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from pokeagent.menus import Menus            # noqa: E402
from pokeagent.naming import NamingScreen    # noqa: E402
from pokeagent.state import NUM_TASKS        # noqa: E402
from pokeagent.trek import Driver            # noqa: E402

log = logging.getLogger("rename")

CITY = "SlateportCity"
HOUSE = "SlateportCity_House1"
#: The Name Rater's object_event (SlateportCity_House1/map.json).
RATER = (7, 5)
#: SlateportCity warp 6 -> MAP_SLATEPORT_CITY_HOUSE1 (SlateportCity/map.json).
CITY_WARP = (5, 19)

#: src/script_menu.c:754-766 -- MSGBOX_YESNO's own task. Its presence is the
#: only honest "a YES/NO box is waiting"; `gMenu`'s bounds are LEFTOVERS
#: until a box is drawn (see Menus.wait_for_choice).
YESNO_TASK = "Task_HandleYesNoInput"
#: src/party_menu.c:448 -- the PARTY_MENU_TYPE_IN_GAME_TRADE input handler.
PICKER_TASK = "HandleSelectPartyMenu"
#: include/constants/pokemon.h / pokeagent.pokemon.NICKNAME_LEN
MAX_NICKNAME = 10
#: `gMain.callback2` once the party screen is BUILT. `CB2_InitPartyMenu` runs
#: first and every press during it is discarded (flying.py:363-367).
PARTY_MAIN_CB2 = "CB2_PartyMenuMain"


# ---- predicates read off the engine ------------------------------------


def fading(d) -> bool:
    """``gPaletteFade.active``. Every d-pad press during a fade is discarded
    (`HandleSelectPartyMenu` runs nothing at all while it is set)."""
    return bool(d.emu.u8(d.emu.resolve("gPaletteFade") + 7) & 0x80)


def yesno_open(d) -> bool:
    return YESNO_TASK in d.state.tasks()


def picker_open(d) -> bool:
    return PICKER_TASK in d.state.tasks()


def picker_slot(d):
    """The party slot the picker's cursor is on, or None.

    ``gSprites[gTasks[t].data[3] >> 8].data[0]`` for the task running
    `HandleSelectPartyMenu` -- `sub_806CA38`, party_menu.c:1773-1787. The
    struct layout and the two strides come from the harness's own measured
    readers rather than from a counted offset.
    """
    b = d.battle
    base = d.emu.resolve("gTasks")
    for i in range(NUM_TASKS):
        addr = base + i * b.task_stride
        if not d.emu.u8(addr + b.task["isActive"]):
            continue
        sym = d.emu.sym.at(d.emu.u32(addr + b.task["func"]) & ~1)
        if not sym or sym.name != PICKER_TASK:
            continue
        sprite_id = d.emu.u16(addr + b.task["data"] + 3 * 2) >> 8
        sprite = d.emu.resolve("gSprites") + sprite_id * b.sprite_stride
        return d.emu.s16(sprite + b.sprite_data)
    return None


def chosen_slot(d) -> int:
    """What the picker actually handed the script.

    `HandleSelectPartyMenu` sets `gSpecialVar_0x8004` to the accepted slot on
    A and to 0xFF on B (script_pokemon_util_80F99CC.c:164-174), and the
    script branches on exactly that -- so this, not our cursor bookkeeping,
    is the truth about which mon is being renamed.
    """
    return d.emu.u16("gSpecialVar_0x8004")


# ---- driving each prompt -----------------------------------------------


def press_until(d, ready, tries=16, seq="A:4 .:28") -> bool:
    """Advance a MSGBOX_DEFAULT chain until `ready()`, checking FIRST.

    Safe in the way a blind A-loop is not: every press is preceded by the
    predicate, and the screens this walks into (`OpenPartyMenuFromScriptContext`,
    the naming screen's `MainState_BeginFadeIn`) both open behind a palette
    fade that discards input, so a press already in flight cannot answer them.
    """
    for _ in range(tries):
        if ready():
            return True
        d.emu.run_sequence(seq)
    return ready()


def answer_yes(d, what: str) -> bool:
    """Walk to a YES/NO box and take YES, cursor verified."""
    if not press_until(d, lambda: yesno_open(d)):
        log.info("  no YES/NO box for %s; the box on screen reads %r",
                 what, (d.state.message() or "")[:90])
        return False
    if not Menus(d.emu, d.state).resolve_choice("YES"):
        log.info("  could not take YES on %s", what)
        return False
    d.settle(300)
    return True


def picker_ready(d) -> bool:
    """The picker is up AND will actually read a d-pad press.

    Three conditions, because two were not enough. `HandleSelectPartyMenu`
    exists as a task while `CB2_InitPartyMenu` is still building the screen,
    and there is a MEASURED gap in that window where `gPaletteFade.active`
    reads 0 -- sampled every 6 frames, the sequence out of
    `OpenPartyMenuFromScriptContext` is:

        cb=CB2_InitPartyMenu  fading=False   <- looks ready, is not
        cb=CB2_PartyMenuMain  fading=True
        cb=CB2_PartyMenuMain  fading=True
        cb=CB2_PartyMenuMain  fading=True
        cb=CB2_PartyMenuMain  fading=False   <- ready

    A cursor drive that started on that first sample lost both of its DOWN
    presses (the first to `CB2_InitPartyMenu`, the second to the fade, since
    `HandleSelectPartyMenu` runs nothing while `gPaletteFade.active`) and
    reported "the picker cursor would not leave slot 0" on a menu that
    steers perfectly well once it has finished drawing.
    """
    return (d.state.callback_name() == PARTY_MAIN_CB2
            and not fading(d)
            and picker_slot(d) is not None)


def wait_picker_ready(d, frames=900, stable=3) -> bool:
    """Wait for `picker_ready` to hold on consecutive samples, not once."""
    seen = 0
    for _ in range(max(1, frames // 6)):
        if picker_ready(d):
            seen += 1
            if seen >= stable:
                return True
        else:
            seen = 0
        d.emu.tick(6)
    return False


def drive_picker(d, target: int, max_presses=16, max_waits=60) -> bool:
    """Put the picker's cursor on party slot `target`, reading every press."""
    if not wait_picker_ready(d):
        log.info("  the picker never settled (cb=%s fading=%s slot=%s) -- "
                 "refusing to press A blind",
                 d.state.callback_name(), fading(d), picker_slot(d))
        return False
    stuck = waits = 0
    for _ in range(max_presses + max_waits):
        cur = picker_slot(d)
        if cur is None:
            log.info("  the picker closed while its cursor was being driven")
            return False
        if cur == target:
            return True
        if not picker_ready(d):
            # A fade or a redraw is running and the press would be discarded.
            # Waiting it out must not spend the stuck budget, or the loop
            # convicts a healthy menu.
            waits += 1
            if waits > max_waits:
                log.info("  the picker never became steerable again "
                         "(cb=%s fading=%s)",
                         d.state.callback_name(), fading(d))
                return False
            d.emu.tick(8)
            continue
        if max_presses <= 0:
            break
        max_presses -= 1
        # Overshooting into CANCEL (7) still converges: the comparison then
        # sends the cursor back UP.
        d.emu.run_sequence("DOWN:4 .:18" if target > cur else "UP:4 .:18")
        if picker_slot(d) == cur:
            stuck += 1
            if stuck > 2:
                log.info("  the picker cursor would not leave slot %s "
                         "(wanted %s)", cur, target)
                return False
        else:
            stuck = 0
    log.info("  the picker cursor never reached slot %s (sits on %s)",
             target, picker_slot(d))
    return False


def settle_keyboard(ns, frames=320) -> bool:
    """Wait for `MainState_HandleInput`; presses outside it go nowhere."""
    for _ in range(max(1, frames // 4)):
        if ns.main_state() == ns.INPUT_STATE:
            return True
        ns.emu.tick(4)
    return ns.main_state() == ns.INPUT_STATE


def clear_buffer(ns, tries=24) -> bool:
    """Empty the pre-filled nickname with B = DeleteTextCharacter."""
    for _ in range(tries):
        if not ns.text():
            return True
        if ns.main_state() != ns.INPUT_STATE:
            ns.emu.tick(8)
            continue
        before = ns.text()
        ns.emu.run_sequence("B:4 .:14")
        if ns.text() == before:
            ns.emu.tick(12)
    return not ns.text()


def type_new_name(d, name: str) -> bool:
    """Clear the old nickname, type `name`, verify the buffer, then OK.

    OK is pressed only once the buffer reads exactly `name`. An empty or
    wrong buffer confirmed here would keep the old nickname
    (`sub_80B74B0`, naming_screen.c:1577-1589) while every log line said the
    rename worked.
    """
    ns = NamingScreen(d.emu, d.state)
    if not ns.is_open():
        log.info("  the naming keyboard never opened")
        return False
    settle_keyboard(ns)
    log.info("  keyboard open, pre-filled with %r", ns.text())

    for attempt in (1, 2):
        if not clear_buffer(ns):
            log.info("  attempt %d: could not clear the buffer (holds %r)",
                     attempt, ns.text())
            continue
        try:
            ns.type(name, confirm=False)
        except Exception as exc:  # noqa: BLE001
            log.info("  attempt %d: typing %r failed: %s",
                     attempt, name, str(exc)[:120])
            continue
        if ns.text() == name:
            break
        log.info("  attempt %d: buffer reads %r, wanted %r",
                 attempt, ns.text(), name)
    else:
        landed = ns.text()
        if not landed:
            # Confirming an empty buffer is a NO-OP that looks like success.
            # There is no cancel on this screen (B is backspace), so the only
            # way out is OK -- taken loudly, and the caller's re-read of the
            # party will report the unchanged name.
            log.info("  buffer is EMPTY; OK here KEEPS the old nickname. "
                     "Confirming to leave the screen, then reporting failure.")
            ns.emu.run_sequence("START:4 .:20 A:4 .:30")
            return False
        log.info("  settling for the partial name %r rather than an empty "
                 "buffer", landed)
        ns.emu.run_sequence("START:4 .:20 A:4 .:30")
        return False

    log.info("  buffer reads %r; pressing OK", ns.text())
    # START -> MoveCursorToOKButton (naming_screen.c:681-685), then A takes it.
    ns.emu.run_sequence("START:4 .:20 A:4 .:30")
    return True


# ---- picking the mon ----------------------------------------------------


def resolve_mon(d, who: str):
    """(index, mon) for `who`, matched on nickname first, then species.

    Refuses an ambiguous species match rather than renaming a coin flip.
    """
    want = who.strip().upper()
    party = d.state.party()
    by_nick = [i for i, m in enumerate(party)
               if not m.is_egg and (m.nickname or "").upper() == want]
    if len(by_nick) == 1:
        return by_nick[0], party[by_nick[0]]
    if len(by_nick) > 1:
        raise SystemExit(f"{want!r} is the nickname of party slots {by_nick}; "
                         f"name the slot you mean with --slot")
    by_species = [i for i, m in enumerate(party)
                  if not m.is_egg
                  and d.names.species(m.species).upper() == want]
    if len(by_species) == 1:
        return by_species[0], party[by_species[0]]
    if len(by_species) > 1:
        raise SystemExit(f"{want!r} matches party slots {by_species}; "
                         f"name the slot you mean with --slot")
    have = [f"{i}:{m.nickname!r}/{d.names.species(m.species)}"
            for i, m in enumerate(party)]
    raise SystemExit(f"no party mon matches {want!r}; party is {have}")


def check_ot(d, index: int, mon) -> bool:
    """Would the rater actually offer to rename this one?"""
    tid = d.emu.u32(d.state._sb2("playerTrainerId"))
    player = d.state.player_name()
    if mon.ot_id != tid:
        log.info("slot %d OT_ID %#x != player %#x -- "
                 "TV_CheckMonOTIDEqualsPlayerID sends the script to "
                 "PlayerNotMonsOT and the rater refuses (tv.c:2088)",
                 index, mon.ot_id, tid)
        return False
    if (mon.ot_name or "").upper() != (player or "").upper():
        log.info("slot %d OT_NAME %r != player name %r -- "
                 "MonOTNameMatchesPlayer refuses (field_specials.c:1900)",
                 index, mon.ot_name, player)
        return False
    return True


def validate_name(d, name: str) -> str:
    """Reject a name the keyboard cannot type, before the trip."""
    name = name.strip().upper()
    if not name:
        raise SystemExit("--name may not be empty: confirming an empty "
                         "buffer KEEPS the old nickname (naming_screen.c:1577)")
    if len(name) > MAX_NICKNAME:
        raise SystemExit(f"{name!r} is {len(name)} characters; the nickname "
                         f"buffer holds {MAX_NICKNAME}")
    ns = NamingScreen(d.emu, d.state)
    missing = [c for c in name if ns.find(c) is None]
    if missing:
        raise SystemExit(f"{missing} are not on the Sapphire naming keyboard")
    return name


# ---- routing ------------------------------------------------------------


def reach_house(d) -> bool:
    """Get into the Name Rater's house from wherever the save sits."""
    import share_grind

    share_grind.unwedge(d)
    if d.map_name() == HOUSE:
        return True

    if d.map_name() != CITY:
        # `to_center` is the only thing that can leave the Elite Four plateau:
        # heal_at_nearest_center() cannot (one-way room chain) and Fly is
        # refused indoors.
        if not d.flight.flyable_here():
            share_grind.to_center(d)
        if not d.flight.flyable_here():
            d.flight.step_outside()
        if not d.fly_to(CITY):
            log.info("could not fly to %s: %s", CITY,
                     getattr(d, "last_fly_reason", "?"))
            return False
        log.info("flew to %s %s", d.map_name(), d.pos())

    # The door is a plain warp on the static grid, but sync anyway: a script
    # that opened something with `setmetatile` leaves nav reading a wall and
    # take_warp then answers "no approach to warp".
    try:
        d.sync_grid()
    except Exception:  # noqa: BLE001
        pass
    if not d.take_warp(*CITY_WARP):
        log.info("take_warp %s refused: %s", CITY_WARP, d.last_warp_reason)
        return False
    d.settle(400)
    if d.map_name() != HOUSE:
        log.info("warp %s landed on %s, not %s", CITY_WARP, d.map_name(), HOUSE)
        return False
    log.info("inside %s at %s", d.map_name(), d.pos())
    return True


def rename(d, index: int, name: str) -> bool:
    """The whole conversation, one verified prompt at a time."""
    if not d.talk_to(*RATER):
        log.info("nothing at %s answered an A press: %s",
                 RATER, d.last_talk_reason)
        return False

    if not answer_yes(d, "'pleased to rate' (MSGBOX_YESNO)"):
        return False

    if not press_until(d, lambda: picker_open(d)):
        log.info("the party picker (%s) never opened; screen reads %r",
                 PICKER_TASK, (d.state.message() or "")[:90])
        return False
    log.info("  party picker open")

    if not drive_picker(d, index):
        return False
    d.emu.run_sequence("A:4 .:36")
    d.settle(600)

    got = chosen_slot(d)
    if got != index:
        log.info("  the picker handed the script slot %s, not %s "
                 "(gSpecialVar_0x8004) -- aborting before the keyboard",
                 got, index)
        return False
    log.info("  gSpecialVar_0x8004 == %d, the slot we wanted", got)

    if not answer_yes(d, "'suggest a better one' (MSGBOX_YESNO)"):
        return False

    ns = NamingScreen(d.emu, d.state)
    if not press_until(d, ns.is_open):
        log.info("the keyboard never opened; screen reads %r",
                 (d.state.message() or "")[:90])
        return False

    ok = type_new_name(d, name)
    d.settle(900)
    d.advance_scene(40_000)
    return ok


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Rename a party mon via the Slateport Name Rater.")
    ap.add_argument("--state", required=True)
    ap.add_argument("--mon", default="CORPHISH",
                    help="nickname or species of the mon to rename")
    ap.add_argument("--name", default=None,
                    help="the new nickname; defaults to the species name, "
                         "which is what declining the catch prompt gives")
    ap.add_argument("--slot", type=int, default=None,
                    help="party index, when --mon is ambiguous")
    ap.add_argument("--out", default=None, help="where to bank the result")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    d = Driver(a.state)
    # A save banked after a Champion win sits on the intro/title: the credits
    # soft-reset the game, SaveBlock RAM still reads plausible, and the player
    # cannot take a single step (trek.py:at_title).
    if not d.resume_from_title():
        log.info("the save is stuck on the boot sequence (%s); nothing can be "
                 "driven from here", d.state.callback_name())
        return 1
    d.advance_scene(40_000)

    if a.slot is not None:
        party = d.state.party()
        if not 0 <= a.slot < len(party):
            raise SystemExit(f"--slot {a.slot} is outside a party of "
                             f"{len(party)}")
        index, mon = a.slot, party[a.slot]
    else:
        index, mon = resolve_mon(d, a.mon)

    species = d.names.species(mon.species).upper()
    name = validate_name(d, a.name if a.name else species)
    log.info("start %s %s", d.map_name(), d.pos())
    log.info("BEFORE: %s", [(i, m.nickname, d.names.species(m.species))
                            for i, m in enumerate(d.state.party())])
    log.info("target: party slot %d, %s nicknamed %r -> %r",
             index, species, mon.nickname, name)

    if mon.is_egg:
        log.info("that slot is an EGG; the rater answers "
                 "'that is merely an egg' and releases (scripts.inc:CantRateEgg)")
        return 1
    if (mon.nickname or "") == name:
        log.info("slot %d is already nicknamed %r; nothing to do", index, name)
        return 0
    if not check_ot(d, index, mon):
        return 1

    if not reach_house(d):
        log.info("could not reach the Name Rater; at %s %s",
                 d.map_name(), d.pos())
        return 1

    ok = rename(d, index, name)

    after = d.state.party()
    log.info("AFTER: %s", [(i, m.nickname, d.names.species(m.species))
                           for i, m in enumerate(after)])
    landed = after[index].nickname if index < len(after) else None
    if landed == name:
        out = a.out or a.state
        d.save(out)
        log.info("*** slot %d is now %r (was %r); banked %s ***",
                 index, landed, mon.nickname, out)
        return 0
    if landed and landed != mon.nickname:
        out = a.out or a.state
        d.save(out)
        log.info("slot %d is now %r -- NOT the %r asked for, but no longer "
                 "%r; banked %s", index, landed, name, mon.nickname, out)
        return 0
    log.info("slot %d still reads %r -- the rename did NOT happen "
             "(rename() returned %s). Nothing banked.",
             index, landed, ok)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
