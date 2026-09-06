#!/usr/bin/env python
"""Breed the three Day Care babies: IGGLYBUFF, PICHU and AZURILL.

Three dex slots no encounter table can supply. All three come out of the
Route 117 Day Care, and the engine picks the species from the MOTHER alone:
``GetEggSpecies`` walks the evolution table backwards from her species
(pret/src/daycare.c:330-366, :645), and Sapphire has no DITTO to stand in for
her, so a FEMALE of the line is a hard requirement -- not a convenience.

Everything below is read out of the cartridge, never counted or assumed:

* **Compatibility** sets how often an egg is offered (daycare.c:862-914): 0
  for same gender, genderless, or non-overlapping egg groups; 50 for the same
  species with the same trainer id; 20 for different species with the same
  trainer id. Every mon on this save shares one OT, so a same-species pair
  scores 50 and a merely egg-group-compatible pair scores 20.
* **The roll** fires once per 256 steps -- ``steps[1] % 256 == 255`` -- and
  succeeds when ``compat > (Random() * 100) / 0xffff`` (daycare.c:759). So
  ~512 steps per egg at 50 and ~1280 at 20, in expectation.
* **Hatching** costs ``eggCycles`` wraps of a u8 step counter plus the wrap
  that reads it at zero: 11 * 256 = **2816** steps, because IGGLYBUFF, PICHU
  and AZURILL all have ``eggCycles = 10``
  (pret/src/data/pokemon/base_stats.h). There is no Flame Body halving in
  R/S; ``_ShouldEggHatch`` has no ability check (daycare.c:738-778).
* **Only real overworld tile steps count.** ``ShouldEggHatch`` is called from
  the per-step handler (pret/src/field_control_avatar.c:583), so Fly, warps
  and bumping into a wall contribute exactly nothing. This script therefore
  never counts presses: it reads
  ``gSaveBlock1.daycare.misc.countersEtc.steps[0]``, a u32 the engine bumps
  once per step for each occupied slot, and reports REAL steps.
* **AZURILL needs SEA INCENSE held by a Day Care parent** or the egg silently
  becomes MARILL (daycare.c:602-622). The item survives the deposit --
  ``StorePokemonInDaycare`` copies the whole box struct (:120-144) -- so it is
  given in the party and then deposited with her.

Walk map is the Day Care's own interior: ``MAP_TYPE_INDOOR``, absent from
``wild_encounters.json``, no trainers, and rows 4 and 5 are open corridors
with no object event on them. Nothing there can interrupt 2816 steps.
"""

import argparse
import logging
import sys
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from pokeagent import cconst, cstruct, paths, pokemon      # noqa: E402
from pokeagent.dex import DexTarget                        # noqa: E402
from pokeagent.fishing import enum_values                  # noqa: E402
from pokeagent.menus import Menus                          # noqa: E402
from pokeagent.naming import NamingScreen                  # noqa: E402
from pokeagent.storage import BOX_COUNT, BOX_SLOTS, Storage  # noqa: E402
from pokeagent.teaching import ITEMS_POCKET, Teacher        # noqa: E402
from pokeagent.trek import Driver, TravelError, TravelInterrupted  # noqa: E402
from share_grind import to_center, unwedge                 # noqa: E402

log = logging.getLogger("breed")

DAYCARE_MAP = "Route117_PokemonDayCare"
#: Route117/map.json warp_events -- the Day Care's door.
DAYCARE_DOOR = (51, 5)
#: Route117_PokemonDayCare/map.json -- the OLD_WOMAN_2 who takes and returns.
CLERK = (2, 2)
#: The Day Care MAN. Route117/map.json places object 2 (local id 3) at (47,4);
#: `setobjectxyperm 3, 47, 6` moves him to hand an egg over
#: (Route117/scripts.inc:10-11) and never moves him back, so try both.
MAN_CELLS = ((47, 6), (47, 4))

#: Babies this script knows how to make, by the ROM's own species name.
BABIES = {"igglybuff": "IGGLYBUFF", "pichu": "PICHU", "azurill": "AZURILL"}
#: `AlterEggSpeciesWithIncenseItem` (daycare.c:602-622). WYNAUT/LAX INCENSE is
#: the same mechanic; WYNAUT is already registered on this save.
INCENSE = {"AZURILL": "SEA INCENSE", "WYNAUT": "LAX INCENSE"}

#: One overworld tile at walking pace. Only used to size a HELD press; the
#: step count itself always comes from the engine's counter.
STEP_FRAMES = 16
#: eggCycles wraps + the wrap that finds friendship already at zero.
HATCH_STEPS = 11 * 256


# --------------------------------------------------------------------------
# ROM facts
# --------------------------------------------------------------------------

class Bio:
    """Species facts from the ROM's own ``gBaseStats``.

    ``Names.base_stats`` stops at ``genderRatio``; breeding needs the egg
    groups and the egg-cycle count too, so the three extra bytes are read at
    offsets taken from the struct's own annotations
    (pret/include/pokemon.h:297-302) against the stride ``Names`` already
    derived from the symbol's size. Nothing is transcribed.
    """

    GENDER_RATIO, EGG_CYCLES, EGG_GROUP1, EGG_GROUP2 = 0x10, 0x11, 0x14, 0x15

    def __init__(self, d):
        self.d = d
        self.stride = d.names.base_stats_stride
        g = cconst.parse_defines(str(paths.CONSTANTS / "pokemon.h"))
        self.MALE, self.FEMALE, self.GENDERLESS = (
            g["MON_MALE"], g["MON_FEMALE"], g["MON_GENDERLESS"])
        groups = enum_values("include/pokemon.h", "EGG_GROUP_UNDISCOVERED")
        self.UNDISCOVERED = groups["EGG_GROUP_UNDISCOVERED"]
        self.DITTO = groups["EGG_GROUP_DITTO"]
        self._cache = {}

    def raw(self, species):
        if species not in self._cache:
            self._cache[species] = bytes(self.d.emu.read(
                ("gBaseStats", species * self.stride), self.stride))
        return self._cache[species]

    def gender_ratio(self, species):
        return self.raw(species)[self.GENDER_RATIO]

    def egg_cycles(self, species):
        return self.raw(species)[self.EGG_CYCLES]

    def egg_groups(self, species):
        r = self.raw(species)
        return (r[self.EGG_GROUP1], r[self.EGG_GROUP2])

    def gender(self, mon):
        """``GetGenderFromSpeciesAndPersonality`` (src/pokemon_2.c)."""
        ratio = self.gender_ratio(mon.species)
        if ratio in (self.MALE, self.FEMALE, self.GENDERLESS):
            return ratio
        return self.FEMALE if ratio > (mon.personality & 0xFF) else self.MALE

    def sex(self, mon):
        return {self.MALE: "M", self.FEMALE: "F"}.get(
            self.gender(mon), "-")

    def compat(self, a, b):
        """``GetDaycareCompatibilityScore`` (pret/src/daycare.c:862-914).

        The DITTO branches are transcribed for completeness even though
        Sapphire has no Ditto -- leaving them out would make this function
        quietly wrong for any save that imported one by trade.
        """
        ga, gb = self.egg_groups(a.species), self.egg_groups(b.species)
        if self.UNDISCOVERED in (ga[0], gb[0]):
            return 0
        if ga[0] == self.DITTO and gb[0] == self.DITTO:
            return 0
        if self.DITTO in (ga[0], gb[0]):
            return 20 if a.ot_id == b.ot_id else 50
        sa, sb = self.gender(a), self.gender(b)
        if sa == sb or self.GENDERLESS in (sa, sb):
            return 0
        if not set(ga) & set(gb):
            return 0
        if a.species == b.species:
            return 50 if a.ot_id == b.ot_id else 70
        return 20 if a.ot_id == b.ot_id else 50


class DayCareRam:
    """``gSaveBlock1.daycare``, laid out from the decomp's annotations.

    ``countersEtc`` is the LAST member of ``struct DayCare``
    (pret/include/global.h:581-584), so its offset is the struct's own span --
    the gap to the next annotated ``SaveBlock1`` field -- minus the counter
    block itself: ``u32 steps[2]``, a ``u16`` and a ``u8``, padded up to the
    u32 alignment (pret/include/global.h:562-566). Deriving it that way means
    a decomp that grew ``struct DayCareMail`` fails the divisibility check
    below instead of reading garbage.
    """

    #: sizeof(struct DayCareStepCountersEtc): 2*4 + 2 + 1, padded to 4.
    COUNTERS_SPAN = 12

    def __init__(self, emu, slots):
        sb1 = cstruct.layout("SaveBlock1")
        self.emu = emu
        self.slots = slots
        self.base = emu.resolve("gSaveBlock1") + sb1["daycare"]
        span = sb1["linkBattleRecords"] - sb1["daycare"]
        mons = slots * pokemon.BOX_SIZE
        mail = span - mons - self.COUNTERS_SPAN
        if mail <= 0 or mail % slots:
            raise ValueError(
                f"struct DayCare spans {span} bytes: {slots} box mons "
                f"({mons}) plus a {self.COUNTERS_SPAN}-byte counter block "
                f"leaves {mail} for {slots} DayCareMail -- refusing to guess")
        self.counters = self.base + mons + mail

    def mon(self, slot):
        raw = bytes(self.emu.read(
            self.base + slot * pokemon.BOX_SIZE, pokemon.BOX_SIZE))
        mon = pokemon.parse_mon(raw)
        return mon if mon is not None and mon.species else None

    def occupants(self):
        return [m for m in (self.mon(s) for s in range(self.slots)) if m]

    def steps(self, slot=0):
        return self.emu.u32(self.counters + 4 * slot)

    def pending(self):
        """``pendingEggPersonality``; non-zero is ``IsEggPending``."""
        return self.emu.u16(self.counters + 4 * self.slots)

    def cycle_counter(self):
        """``eggCycleStepsRemaining`` -- a u8 that wraps every 256 steps."""
        return self.emu.u8(self.counters + 4 * self.slots + 2)


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------

def species_id(d, name):
    want = str(name).upper()
    for i in range(1, d.names.species_count):
        if d.names.species(i).upper() == want:
            return i
    raise KeyError(f"{name!r} is not a species this ROM knows")


def in_field(d) -> bool:
    return (d.state.callback_name() or "") in (
        "CB2_Overworld", "CB2_OverworldBasic")


def leave_title(d) -> bool:
    """Get off the title screen a post-credits save boots on.

    A Hall of Fame run rolls the credits and SOFT RESETS, so the savestate
    sits on the title with SaveBlock RAM intact: map, party and dex all read
    plausibly while the player cannot move and every ``goto`` reports
    "stalled". ``Driver.at_title`` / ``Driver.resume_from_title`` own that
    recovery -- there is exactly one copy of it, in the library.
    """
    if not d.at_title():
        return True
    log.info("title screen (%s) -- taking CONTINUE",
             d.state.callback_name())
    if not d.resume_from_title():
        return False
    log.info("in the field at %s %s", d.map_name(), d.pos())
    return True


def party_of(d):
    return [m for m in d.state.party() if m.species]


def slot_of(d, personality):
    for i, m in enumerate(d.state.party()):
        if m.species and m.personality == personality:
            return i
    return None


def show_party(d, bio):
    return [f"{d.names.species(m.species)}{bio.sex(m)}"
            f"{'/EGG' if m.is_egg else ''}" for m in party_of(d)]


def boxed(d):
    """Every box mon, as ``(box, slot, Mon)``."""
    base = d.emu.resolve("gPokemonStorage") + 4      # boxes[] at +0x4
    span = BOX_COUNT * BOX_SLOTS * pokemon.BOX_SIZE
    blob = bytes(d.emu.read(base, span))
    out = []
    for b in range(BOX_COUNT):
        for s in range(BOX_SLOTS):
            off = (b * BOX_SLOTS + s) * pokemon.BOX_SIZE
            mon = pokemon.parse_mon(blob[off:off + pokemon.BOX_SIZE])
            if mon is not None and mon.checksum_ok and mon.species:
                out.append((b, s, mon))
    return out


# --------------------------------------------------------------------------
# picking parents
# --------------------------------------------------------------------------

def egg_species(evo, species):
    """``GetEggSpecies`` (pret/src/daycare.c:330-366) -- the chain's root."""
    roots = evo.roots(species)
    return roots[0] if roots else species


def find_parents(d, bio, evo, baby):
    """A mother whose egg is `baby`, and the best available father.

    Returns ``(mother, father, where)`` where each parent is
    ``(box, slot, Mon)`` with ``box`` None for a party member. Fathers are
    ranked by the compatibility the ROM would score them at, so a same-species
    pair (50) always beats a merely egg-group-compatible one (20) -- which is
    2.5x fewer steps per egg.
    """
    baby_id = species_id(d, baby)
    pool = [(None, i, m) for i, m in enumerate(d.state.party())
            if m.species and not m.is_egg] + boxed(d)

    mothers = [p for p in pool
               if bio.gender(p[2]) == bio.FEMALE
               and egg_species(evo, p[2].species) == baby_id]
    if not mothers:
        lines = sorted({d.names.species(p[2].species) for p in pool
                        if egg_species(evo, p[2].species) == baby_id})
        return None, None, (
            f"no FEMALE of the {baby} line is owned"
            + (f" (males on hand: {', '.join(lines)})" if lines else "")
            + " -- and Sapphire has no DITTO, so the mother's species IS the "
              "egg's species (daycare.c:622-645)")

    best = None
    for mother in mothers:
        for father in pool:
            if father[2].personality == mother[2].personality:
                continue
            score = bio.compat(mother[2], father[2])
            if score and (best is None or score > best[0]):
                best = (score, mother, father)
    if best is None:
        return None, None, (
            f"no compatible partner for any female "
            f"{d.names.species(mothers[0][2].species)}")
    score, mother, father = best
    where = (f"compat {score} "
             f"({d.names.species(mother[2].species)}F x "
             f"{d.names.species(father[2].species)}M)")
    return mother, father, where


# --------------------------------------------------------------------------
# getting there
# --------------------------------------------------------------------------

def to_pc(d):
    """Stand in a real Pokemon Center, which is where the PC work happens.

    NOT the Day Care: ``Storage.pc_cells()`` reports a PC at
    ``Route117_PokemonDayCare (10,1)`` and there is none -- it is a metatile
    behaviour misdetection, and a deposit driven at it presses A into thin
    air. Only a map whose name ends ``PokemonCenter_1F`` is trusted.
    """
    if d.map_name().endswith("PokemonCenter_1F"):
        return True
    unwedge(d)
    for _ in range(6):
        if d.flight.flyable_here():
            break
        before = d.map_name()
        try:
            d.flight.step_outside()
        except Exception as exc:            # noqa: BLE001
            log.info("  step_outside: %s", str(exc)[:70])
            break
        if d.map_name() == before:
            break
    # MAUVILLE, deliberately: its Centre is one map from Route 117, so the
    # walk to the Day Care after the PC work is a single `travel` leg.
    for town in ("MauvilleCity", "VerdanturfTown"):
        try:
            if d.fly_to(town):
                break
        except Exception as exc:            # noqa: BLE001
            log.info("  fly %s: %s", town, str(exc)[:70])
    try:
        d.heal_at_nearest_center()
    except Exception as exc:                # noqa: BLE001
        log.info("  heal: %s", str(exc)[:80])
    if d.map_name().endswith("PokemonCenter_1F"):
        return True
    return to_center(d)


def to_daycare(d):
    """Inside ``Route117_PokemonDayCare``."""
    if d.map_name() == DAYCARE_MAP:
        return True
    for _ in range(3):
        if d.map_name() == "Route117":
            break
        try:
            d.travel("Route117", on_battle="fight", budget_s=240)
        except TravelInterrupted:
            d.fight()
            d.advance_scene(40_000)
        except TravelError as exc:
            log.info("  travel Route117: %s", str(exc)[:110])
            break
    if d.map_name() == "Route117":
        d.sync_grid()
        if not d.take_warp(*DAYCARE_DOOR):
            log.info("  door %s: %s", DAYCARE_DOOR, d.last_warp_reason)
    return d.map_name() == DAYCARE_MAP


def leave_daycare(d):
    """Back out onto Route 117.

    ``travel`` rather than ``take_warp``: the interior's own warps sit on
    (2,8)/(3,8) with elevation 0 and a run that fired them directly answered
    "no approach to warp" more often than it worked.
    """
    if d.map_name() == "Route117":
        return True
    for _ in range(3):
        try:
            d.travel("Route117", on_battle="fight", budget_s=200)
        except TravelInterrupted:
            d.fight()
            d.advance_scene(40_000)
        except TravelError as exc:
            log.info("  leave: %s", str(exc)[:110])
        if d.map_name() == "Route117":
            return True
    return False


# --------------------------------------------------------------------------
# the walk
# --------------------------------------------------------------------------

def corridor(d):
    """The longest straight open run on this map, as ``(y, x0, x1)``.

    Only ``floor`` cells with no collision and no live body on them count, so
    the run never includes a warp (which would end the walk), grass (an
    encounter), or an NPC (a bump, which is NOT a step -- the engine's own
    counter proves it, and a previous attempt burned ~5000 presses on exactly
    that).
    """
    grid = d.nav.grid(d.map_name())
    # `live_npcs` puts the PLAYER at index 0; excluding his own cell would
    # split the corridor he is standing in.
    bodies = {(n["x"], n["y"]) for n in d.live_npcs() if not n["player"]}

    def ok(cell, xy):
        return (cell is not None and cell.collision == 0
                and (cell.kind or "") == "floor" and xy not in bodies)

    best = None
    for y, row in enumerate(grid):
        x = 0
        while x < len(row):
            if not ok(row[x], (x, y)):
                x += 1
                continue
            x0 = x
            while x < len(row) and ok(row[x], (x, y)):
                x += 1
            if best is None or (x - x0) > (best[2] - best[1] + 1):
                best = (y, x0, x - 1)
    if best is None or best[2] - best[1] < 2:
        raise RuntimeError(f"no walkable corridor on {d.map_name()}")
    return best


def hoof(d, dc, want, why, budget_s=900.0):
    """Take `want` REAL overworld steps, counted by the engine.

    ``steps[0]`` is incremented once per step for each occupied Day Care slot
    (daycare.c:738-750), so with both parents deposited it is an exact,
    non-wrapping step counter -- the only honest way to report progress, since
    a held press that bumps a wall looks identical to one that moves.

    Returns ``(real_steps, stopped_by)``.
    """
    lane = corridor(d)
    y, x0, x1 = lane
    span = x1 - x0
    if d.pos()[1] != y or not (x0 <= d.pos()[0] <= x1):
        if not d.goto(x0, y):
            raise RuntimeError(
                f"cannot reach the walking lane {lane} on {d.map_name()}: "
                f"{d.last_goto_reason}")
    # One extra tile of hold covers the turn-in-place at each end, which
    # costs frames but is not a step.
    frames = (span + 1) * STEP_FRAMES
    start = dc.steps(0)
    log.info("  walking %s on %s row %d x%d-%d for %d steps (from %d)",
             why, d.map_name(), y, x0, x1, want, start)
    deadline = time.time() + budget_s
    mark = 0
    while True:
        done = dc.steps(0) - start
        if done >= want:
            return done, "done"
        if time.time() > deadline:
            log.info("  out of walking budget at %d/%d steps", done, want)
            return done, "budget"
        for key in ("RIGHT", "LEFT"):
            d.emu.run_sequence(f"{key}:{frames}")
            if d.scene_active():
                return dc.steps(0) - start, "scene"
        done = dc.steps(0) - start
        if done - mark >= 256:
            mark = done
            egg = next((m for m in d.state.party() if m.is_egg), None)
            log.info("    %d/%d steps%s", done, want,
                     f", egg cycles left {egg.friendship}" if egg else "")


# --------------------------------------------------------------------------
# the Day Care clerk
#
# Her whole script is a chain of `msgbox ... MSGBOX_YESNO` boxes whose MEANING
# depends on `GetDaycareState` (pret/src/daycare.c:818-834), and the states do
# not agree on what the first question is:
#
#   0  "Should I raise a POKeMON for you?"   YES -> the party picker
#   2  a level report, then "another one?"   YES -> the party picker
#                          then "take one back?"
#   3  a level report x2, then "take one back?"  YES -> the withdraw menu
#
# So a blind A -- which is what `advance_scene` does, and what `Driver`'s own
# YES default does -- means YES to whichever question happens to be up. That
# cost a whole run once: after one good deposit the script immediately asked
# "another one?", `advance_scene` answered YES, the picker reopened and the
# next blind A stored the party LEAD; the day care then read full, so the
# second call fell down the state-3 path, answered YES to "take one back?"
# and swapped both parents out for whatever the cursor was on. Every answer
# below is therefore chosen by the caller from the state it READ.
# --------------------------------------------------------------------------

#: The task that owns a script YES/NO box (`ScriptMenu_YesNo` ->
#: `Task_HandleYesNoInput`, pret/src/script_menu.c:754-766). `choice_open()`
#: goes by `gMenu`'s bounds instead, and those are LEFTOVERS while a message
#: box is still printing -- a false positive there answers a question that is
#: not on screen.
YESNO_TASK = "Task_HandleYesNoInput"
#: `ChooseSendDaycareMon` -> `OpenPartyMenu` (pret/src/daycare.c:1071-1075).
PICKER_CB = "CB2_PartyMenuMain"
#: The party list's own input TASKS: the Day Care picker
#: (pret/src/choose_party.c:812) and the ordinary field one
#: (pret/src/pokemon_menu.c:253). The field route installs its callback via
#: `sub_808AD58`, NOT `OpenPartyMenu`, so keying off `CB2_PartyMenuMain`
#: alone made every field give bail out before it started -- "give attempt 1
#: did not land (holding nothing)", four times, with the list plainly up.
PICKER_TASKS = ("HandleDaycarePartyMenu", "HandleDefaultPartyMenu",
                "Task_DaycareStorageMenu8122EAC")
#: The "which one?" list a withdrawal raises (`ShowDaycareLevelMenu`,
#: pret/src/daycare.c:1059-1069). Its handler keeps the index in
#: `gTasks[].data[0]` but moves `gMenu.cursorPos` in lockstep (:1020-1046),
#: so `Menus.select_index` drives it; index `slots` is CANCEL.
LEVEL_MENU_TASK = "HandleDaycareLevelMenuInput"


def picker_up(d) -> bool:
    """Is a party list on screen and taking input?"""
    return (d.state.callback_name() == PICKER_CB
            or bool(set(d.state.tasks()) & set(PICKER_TASKS)))


def _drive_clerk(d, menus, answers, test, tries=40):
    """Advance the clerk's dialogue to `test()`, answering YES/NO in order.

    `answers` is consumed one entry per YES/NO box that actually appears;
    anything after the list is exhausted is answered NO, which is always the
    inert choice. Message boxes take a B press -- `text.c:2412` watches
    `A_BUTTON | B_BUTTON` -- so nothing here ever presses A, and no stray
    press can be read as a YES.
    """
    pending = list(answers)
    for _ in range(tries):
        if test():
            return True
        if YESNO_TASK in d.state.tasks():
            menus.resolve_choice(pending.pop(0) if pending else "NO")
            d.settle(800)
            continue
        d.emu.run_sequence("B:6 .:70")
        d.settle(500)
    return test()


def _close_clerk(d, menus, tries=24):
    """Leave the conversation without answering YES to anything.

    A completed STORE drops straight into "another one?" and a completed
    withdrawal into "the other one too?"; both are answered NO. The party
    picker, if it is somehow up, is cancelled with B (which writes
    `gLastFieldPokeMenuOpened = 0xFF`, pret/src/choose_party.c:825-830).
    """
    for _ in range(tries):
        if not d.scene_active() and not d.at_title():
            return True
        if picker_up(d):
            d.emu.run_sequence("B:6 .:70")
        elif YESNO_TASK in d.state.tasks():
            menus.resolve_choice("NO")
        else:
            d.emu.run_sequence("B:6 .:70")
        d.settle(700)
    return not d.scene_active()


def _pick_in_party_menu(d, menus, want, confirm=None, rounds=9) -> bool:
    """Choose party slot `want` in a party list, verified before committing.

    Two engine facts make this exact rather than hopeful:

    * A on the party list writes the chosen PARTY index to
      `gLastFieldPokeMenuOpened` (pret/src/choose_party.c:820) BEFORE the
      STORE/SUMMARY/EXIT popup is drawn -- so the pick is READ and a wrong one
      is backed out with B. Nothing here trusts a cursor offset, which matters
      because the picker index does not map to the party index (RSE draws slot
      0 as a tall box and 1-5 in a two-column grid).
    * DOWN walks the list and wraps through CANCEL,
      `0 -> 1 -> ... -> count-1 -> 7 -> 0` (`ChangeDefaultPartyMenuSelection`,
      pret/src/party_menu.c:1380-1420), so a bounded ring walk reaches every
      slot.

    `confirm` is what to do once the right mon is selected and its per-mon
    popup is open; the default is `select_index(0)`, which is STORE in the Day
    Care popup (STORE / SUMMARY / EXIT, pret/src/choose_party.c:781-785). The
    FIELD party menu writes the same variable from the same input handler
    (pret/src/pokemon_menu.c:259-268), so this drives that one too.
    """
    if confirm is None:
        confirm = lambda: menus.select_index(0)         # noqa: E731
    for _ in range(rounds):
        if not picker_up(d):
            return False
        d.emu.run_sequence("A:6 .:60")
        d.settle(700)
        chose = d.emu.u8("gLastFieldPokeMenuOpened")
        if chose == want:
            return bool(confirm())
        log.debug("    picker landed on %s, wanted %s", chose, want)
        d.emu.run_sequence("B:6 .:60")                  # leave the popup
        d.settle(600)
        if not picker_up(d):
            return False
        d.emu.run_sequence("DOWN:6 .:40")
        d.settle(400)
    return False


def give_held(d, menus, teacher, personality, item, tries=4) -> bool:
    """Make the mon with `personality` hold `item`. Judged on ITS held_item.

    `Teacher.give_from_field` takes a party SLOT and steers the list off a
    sprite offset, and the picker index is not the party index -- so it handed
    this save's SEA INCENSE to a neighbour and then reported "slot 4 holds 0
    after the give", with the only copy in the game now on the wrong mon. The
    field party menu writes the chosen PARTY index to
    `gLastFieldPokeMenuOpened` before it opens the per-mon popup
    (pret/src/pokemon_menu.c:259-268), so the pick is verifiable here in
    exactly the way the Day Care deposit is.

    Row order in the popup is data-driven -- field moves come first -- so ITEM
    is `max - 1` and its submenu is GIVE / TAKE / CANCEL, GIVE at 0
    (`ShowPartyPopupMenu` -> `InitMenu`, pret/src/party_menu.c:2847-2856).

    KNOWN BLOCKER, measured: everything up to and including the bag opening
    works -- the debug trace reads `popup bounds 0..3`, ITEM taken,
    `give/take bounds 0..2`, GIVE taken, `bag open on the ITEMS pocket` -- and
    then the A press on an item ROW hands nothing over, on every row, four
    attempts running. `Teacher.give_from_field` fails at exactly the same
    point ("slot 4 holds 0 after the give") and so does the bag-first
    `give_to_mon` ("no party member matches"). Nothing in the ITEMS pocket can
    currently be equipped by this harness, which is why AZURILL is blocked:
    the SEA INCENSE is in the bag and cannot be put on a parent.
    """
    item_id = teacher._item_id(item)
    if not item_id:
        log.info("  %r is not an item this ROM knows about", item)
        return False
    if any(m.personality == personality and m.held_item == item_id
           for m in d.state.party()):
        return True
    # CHECK THE POCKET BEFORE ANY MENU IS OPEN: `gBagPockets` is re-pointed
    # while the bag UI is up, so the same read that lists the item on the
    # overworld answers "not in the ITEMS pocket" once the give flow started.
    if not any(iid == item_id
               for _s, iid, _q in teacher.pocket_items(ITEMS_POCKET)):
        log.info("  %s is not in the ITEMS pocket", item)
        return False

    def held():
        return next((m.held_item for m in d.state.party()
                     if m.personality == personality), None)

    def hand_over():
        """Inside the per-mon popup for the right mon: ITEM -> GIVE -> bag."""
        lo, hi = menus.bounds()
        log.debug("    popup bounds %d..%d", lo, hi)
        if hi - lo < 2 or not menus.select_index(hi - 1):        # ITEM
            log.debug("    ITEM (row %d) refused: %s", hi - 1,
                      menus.last_reason)
            return False
        d.settle(900)
        lo2, hi2 = menus.bounds()
        log.debug("    give/take bounds %d..%d", lo2, hi2)
        if not menus.select_index(0):                            # GIVE
            log.debug("    GIVE refused: %s", menus.last_reason)
            return False
        d.settle(900)
        if not teacher._reach_pocket(ITEMS_POCKET):
            log.debug("    bag never reached the ITEMS pocket: %s",
                      teacher.last_reason)
            return False
        log.debug("    bag open on the ITEMS pocket")
        # PRESS AND VERIFY, ROW BY ROW. Every read of the bag list is
        # untrustworthy while the bag UI is open, so the only reliable signal
        # is what the mon ends up holding. A wrong item is handed straight
        # back rather than left on it.
        for _ in range(14):
            d.emu.run_sequence("A:6 .:120")
            d.settle(600)
            now = held()
            if now == item_id:
                return True
            if now:
                d.close_menus()
                d.settle(300)
                teacher.take_from_mon(slot_of(d, personality) or 0)
                return False
            d.emu.run_sequence("DOWN:6 .:40")
            d.settle(250)
        return False

    for attempt in range(tries):
        if held() == item_id:
            return True
        want = slot_of(d, personality)
        if want is None:
            log.info("  personality %#x is not in the party", personality)
            return False
        unwedge(d)
        d.close_menus()
        d.settle(300)
        d.emu.run_sequence("START:6 .:90")
        d.settle(600)
        if not menus.select_index(1):                            # POKeMON
            d.close_menus()
            continue
        d.settle(900)
        log.debug("  picker up=%s cb=%s tasks=%s", picker_up(d),
                  d.state.callback_name(), d.state.tasks())
        picked = _pick_in_party_menu(d, menus, want, confirm=hand_over)
        log.debug("  pick+give -> %s (wanted slot %d)", picked, want)
        d.close_menus()
        d.settle(400)
        if held() == item_id:
            log.info("  %s now holds %s",
                     d.names.species(next(m.species for m in d.state.party()
                                          if m.personality == personality)),
                     item)
            return True
        log.info("  give attempt %d did not land (holding %s)", attempt + 1,
                 d.names.item(held()) if held() else "nothing")
    # LAST RESORT: the BAG's own give flow. The party route above reaches the
    # bag correctly -- verified: popup bounds 0..3, ITEM taken, GIVE taken,
    # "bag open on the ITEMS pocket" -- and then the A press on the item row
    # hands nothing over, which is the same wall `Teacher.give_from_field`
    # hits. `give_to_mon` starts from the BAG instead and picks the mon last,
    # so it fails differently; it is judged on the same held_item, and a wrong
    # recipient is fine as long as it is the OTHER day care parent, since
    # `AlterEggSpeciesWithIncenseItem` tests both (pret/src/daycare.c:602-622).
    mon = next((m for m in d.state.party()
                if m.personality == personality), None)
    if mon is not None:
        try:
            teacher.give_to_mon(item, mon)
        except Exception as exc:            # noqa: BLE001
            log.info("  bag give: %s", str(exc)[:90])
        d.close_menus()
        d.settle(400)
        if held() == item_id:
            log.info("  %s now holds %s (via the bag)",
                     d.names.species(mon.species), item)
            return True
        landed = [d.names.species(m.species) for m in d.state.party()
                  if m.species and m.held_item == item_id]
        if landed:
            log.info("  the bag give put %s on %s instead", item, landed)
            return False
    return False


def daycare_store(d, dc, menus, personality, tries=4):
    """Deposit the party mon with `personality`. Judged on the day care.

    Refuses outright when the day care is already full: at state 3 the clerk
    has no deposit branch at all, and her only YES/NO is the one that hands
    mons BACK.
    """
    for attempt in range(tries):
        inside = dc.occupants()
        if any(m.personality == personality for m in inside):
            return True
        if len(inside) >= dc.slots:
            log.info("  day care is full (%s) -- cannot deposit",
                     [d.names.species(m.species) for m in inside])
            return False
        before = {m.personality for m in inside}
        # `CompactPartySlots` runs after every store, so the target slot is
        # re-read each attempt rather than cached.
        want = slot_of(d, personality)
        if want is None:
            log.info("  personality %#x is not in the party", personality)
            return False
        unwedge(d)
        try:
            d.talk_to(*CLERK)
        except TravelInterrupted:
            d.fight()
            continue
        except Exception as exc:            # noqa: BLE001
            log.info("  talk_to clerk: %s", str(exc)[:90])
        # One YES either way: "raise a POKeMON?" at state 0, "another one?"
        # at state 2. Both reach `ChooseSendDaycareMon`.
        if not _drive_clerk(d, menus, ["YES"],
                            lambda: picker_up(d)):
            log.info("  the picker never opened (cb %s, tasks %s)",
                     d.state.callback_name(), d.state.tasks())
            _close_clerk(d, menus)
            continue
        _pick_in_party_menu(d, menus, want)
        _close_clerk(d, menus)
        now = {m.personality for m in dc.occupants()}
        if personality in now:
            log.info("  deposited %s -- day care now %s",
                     d.names.species(next(
                         m for m in dc.occupants()
                         if m.personality == personality).species),
                     [d.names.species(m.species) for m in dc.occupants()])
            return True
        if now - before:
            raise RuntimeError(
                "stored the WRONG mon: the day care holds "
                f"{[d.names.species(m.species) for m in dc.occupants()]}. "
                "Refusing to continue so the state is not saved over.")
        log.info("  deposit attempt %d did not land", attempt + 1)
    return False


def daycare_retrieve(d, dc, menus, personality, tries=4):
    """Take the mon with `personality` back out of the Day Care.

    Needed between babies: the second run's parents cannot go in while the
    first run's are still there. It costs money (`GetDaycareCost`) and needs a
    party slot free (`CalculatePlayerPartyCount / compare 6`,
    ``..._EventScript_1B2469``), and the question chain DIFFERS BY STATE:

      state 3, both slots filled: "take one back?" YES -> the which-one list
                                  (`ShowDaycareLevelMenu`) -> price YES
      state 2, one slot filled:   "raise another one?" NO -> "take one back?"
                                  YES -> price YES, and no list at all

    Getting that count wrong is a silent no-op, not an error: the first
    version passed only ["NO", "YES"] at state 2, so the PRICE box fell
    through to the default NO and four attempts in a row reported "did not
    land" while the clerk politely cancelled each one.
    """
    for attempt in range(tries):
        inside = dc.occupants()
        slot = next((i for i in range(dc.slots)
                     if (m := dc.mon(i)) and m.personality == personality),
                    None)
        if slot is None:
            return True
        if len(party_of(d)) >= 6:
            log.info("  party is full -- the clerk refuses to hand one back")
            return False
        full = len(inside) >= dc.slots
        answers = ["YES"] if full else ["NO", "YES", "YES"]
        unwedge(d)
        try:
            d.talk_to(*CLERK)
        except TravelInterrupted:
            d.fight()
            continue
        except Exception as exc:            # noqa: BLE001
            log.info("  talk_to clerk: %s", str(exc)[:90])
        # With both slots filled the script raises the which-one list; with
        # one it goes straight to the price.
        if full:
            if not _drive_clerk(d, menus, answers,
                                lambda: LEVEL_MENU_TASK in d.state.tasks()):
                log.info("  the withdraw list never opened (tasks %s)",
                         d.state.tasks())
                _close_clerk(d, menus)
                continue
            if not menus.select_index(slot):
                log.info("  could not select day care slot %d: %s",
                         slot, menus.last_reason)
                _close_clerk(d, menus)
                continue
            d.settle(900)
            answers = ["YES"]               # the price box is next
        gone = (lambda: not any(m and m.personality == personality
                                for m in (dc.mon(i)
                                          for i in range(dc.slots))))
        _drive_clerk(d, menus, answers, gone)
        _close_clerk(d, menus)
        if gone():
            log.info("  withdrew %s -- day care now %s",
                     d.names.species(inside[slot].species) if
                     slot < len(inside) else "?",
                     [d.names.species(m.species) for m in dc.occupants()])
            return True
        log.info("  withdraw attempt %d did not land", attempt + 1)
    return False


_pending_dc = None


def _pending(d) -> bool:
    return bool(_pending_dc.pending()) if _pending_dc else False


def reject_egg(d, menus, tries=4) -> bool:
    """Turn a pending egg DOWN, so the clerk will talk business again.

    A pending egg puts `GetDaycareState` at 1 (pret/src/daycare.c:818-834),
    and at state 1 the Old Woman's whole script is one message and a release
    (``..._EventScript_1B2407``) -- no deposit branch, no withdraw branch. So
    a run that leaves a stray egg pending cannot change the day care at all:
    four eviction attempts in a row reported "the withdraw list never opened"
    with nothing wrong but the egg.

    The MAN outside asks twice and NO to both runs `RejectEggFromDayCare`
    (Route117/scripts.inc, ``_1B2262``), which clears
    `pendingEggPersonality` and the flag. That is the point here: this is only
    ever called on an egg belonging to a pair we are about to evict.
    """
    if not leave_daycare(d):
        log.info("  could not get onto Route117 to refuse the egg")
        return False
    d.sync_grid()
    live = {(n["x"], n["y"]) for n in d.live_npcs() if not n["player"]}
    cells = [c for c in MAN_CELLS if c in live] or list(MAN_CELLS)
    for attempt in range(tries):
        if not _pending(d):
            return True
        cell = cells[attempt % len(cells)]
        try:
            d.talk_to(*cell)
        except TravelInterrupted:
            d.fight()
            continue
        except Exception as exc:            # noqa: BLE001
            log.info("  talk_to man %s: %s", cell, str(exc)[:90])
            continue
        # He asks twice ("...want it?" then "...are you sure?"); NO to both.
        _drive_clerk(d, menus, ["NO", "NO"], lambda: not _pending(d))
        _close_clerk(d, menus)
        if not _pending(d):
            log.info("  refused the pending egg at %s", cell)
            return True
    return not _pending(d)


def clear_daycare(d, dc, menus, keep):
    """Empty the Day Care of everything that is not in `keep`.

    `keep` is a set of personalities. `ShiftDaycareSlots` compacts slot 1 into
    slot 0 on a withdrawal (pret/src/daycare.c:152-165), so the loop re-reads
    the occupants every time rather than iterating an index.
    """
    global _pending_dc
    _pending_dc = dc
    for _ in range(dc.slots + 1):
        stray = next((m for m in dc.occupants()
                      if m.personality not in keep), None)
        if stray is None:
            return True
        if dc.pending():
            log.info("  an egg from the outgoing pair is pending -- the clerk "
                     "will not trade while it is (state 1)")
            if not reject_egg(d, menus):
                return False
            if not to_daycare(d):
                return False
        log.info("  day care holds %s, which is not a parent for this egg",
                 d.names.species(stray.species))
        if not daycare_retrieve(d, dc, menus, stray.personality):
            return False
    return not any(m.personality not in keep for m in dc.occupants())


def collect_egg(d, dc, menus, tries=6):
    """Take the pending egg off the Day Care MAN on Route 117.

    His script (``Route117_EventScript_1B222D`` -> ``_1B2262``) asks twice and
    only takes YES; with a party of six it refuses outright
    (``CalculatePlayerPartyCount`` / ``compare 6``), which is why a slot is
    kept free before any of this starts.
    """
    def has_egg():
        return any(m.is_egg for m in d.state.party())

    # He is object 2 in Route117/map.json but `setobjectxyperm 3, 47, 6`
    # relocates him and never puts him back, so ask the live table which of
    # the two cells he is actually standing on.
    live = {(n["x"], n["y"]) for n in d.live_npcs() if not n["player"]}
    cells = [c for c in MAN_CELLS if c in live] or list(MAN_CELLS)
    for attempt in range(tries):
        if has_egg():
            return True
        cell = cells[attempt % len(cells)]
        # NEVER `unwedge` mid-conversation here: it answers an open choice
        # with NO, and NO to this man runs `RejectEggFromDayCare`
        # (Route117/scripts.inc, ``_1B2262``) -- the egg is destroyed and the
        # 500-odd steps that produced it are gone. Only YES and A are safe.
        try:
            d.talk_to(*cell)
        except TravelInterrupted:
            d.fight()
            d.advance_scene(40_000)
            continue
        except Exception as exc:            # noqa: BLE001
            log.info("  talk_to man %s: %s", cell, str(exc)[:90])
            continue
        for _ in range(20):
            if has_egg():
                break
            if d.choice_open():
                menus.resolve_choice("YES")
                d.settle(600)
                continue
            d.emu.run_sequence("A:6 .:70")
            d.settle(400)
        d.advance_scene(40_000)
        if has_egg():
            log.info("  egg received from the DAY-CARE MAN at %s", cell)
            return True
        log.info("  attempt %d: no egg yet (pending %#x, party %d/6)",
                 attempt + 1, dc.pending(),
                 len([m for m in d.state.party() if m.species]))
    return has_egg()


def dismiss_scene(d, menus, tries=80):
    """Ride out a hatch or a catch and decline the nickname keyboard.

    ``CB2_EggHatch`` prints the prompt and raises a real YES/NO box
    (``InitYesNoMenu``, pret/src/egg_hatch.c:544-556); YES opens the naming
    keyboard, which then owns every input until it is completed -- that is what
    wedged the canonical save for 17 minutes once before. NO (index 1) skips
    it entirely. A catch's own "give a nickname?" box is the same shape, so
    the Safari hunt uses this too. The keyboard is still handled, by
    COMPLETING it rather than dodging it, in case a stray A gets there first
    -- but `accept()` on an EMPTY keyboard silently keeps the old name, so it
    is a recovery path only, never the plan.
    """
    ns = NamingScreen(d.emu, d.state)
    for _ in range(tries):
        if in_field(d) and not d.scene_active():
            return True
        try:
            if ns.is_open():
                log.info("  naming screen open: accepting %r", ns.accept())
                d.advance_scene(60_000)
                continue
        except Exception as exc:            # noqa: BLE001
            log.debug("  naming: %s", str(exc)[:70])
        try:
            lo, hi = menus.bounds()
        except Exception:                   # noqa: BLE001
            lo, hi = 0, 0
        if hi - lo == 1 and menus.select_index(1):      # YES/NO -> NO
            d.settle(900)
            continue
        d.emu.run_sequence("A:6 .:80")
        d.settle(500)
    return in_field(d) and not d.scene_active()


# --------------------------------------------------------------------------
# staging the party
# --------------------------------------------------------------------------

def stage_party(d, bio, mother, father, keep_free=0):
    """Put both parents in the party, benching the least useful members.

    Only as many slots are freed as the withdraws need: the Day Care deposit
    hands both of them straight back, so the egg's slot comes for free. Every
    extra PC round trip is another chance for the storage UI to land somewhere
    unexpected, so this does the minimum.

    A parent already in the party is never benched, and neither is a
    field-move carrier -- losing FLY strands the run.
    """
    st = Storage(d)
    need = [p for p in (mother, father) if p[0] is not None]
    parents = {p[2].personality for p in (mother, father)}
    room = len(need) + keep_free

    while len(party_of(d)) > 6 - room:
        keepers = _keepers(d)
        victims = [(m.level or 0, i) for i, m in enumerate(d.state.party())
                   if m.species and i not in keepers
                   and m.personality not in parents]
        if not victims:
            victims = [(m.level or 0, i) for i, m in enumerate(d.state.party())
                       if m.species and m.personality not in parents]
        if not victims:
            return False, "nothing in the party is expendable"
        victims.sort()
        idx = victims[0][1]
        name = d.names.species(d.state.party()[idx].species)
        if not st.deposit(idx):
            return False, f"could not deposit {name}: {st.last_reason}"
        log.info("  benched %s -> PC (party %s)", name, show_party(d, bio))

    for box, slot, mon in need:
        name = d.names.species(mon.species)
        if not st.withdraw(box, slot):
            return False, (f"could not withdraw {name} from box {box} "
                           f"slot {slot}: {st.last_reason}")
        log.info("  withdrew %s (party %s)", name, show_party(d, bio))
    return True, "staged"


def _keepers(d):
    """Party indices that must not be benched: the field-move carriers.

    Losing FLY strands the run; losing SURF strands ``nav`` on the wrong side
    of every channel.
    """
    keep = set()
    ids = {}
    try:
        ids = d.field_moves() or {}
    except Exception:                       # noqa: BLE001
        pass
    wanted = {"FLY", "SURF"}
    for i, m in enumerate(d.state.party()):
        if not m.species:
            continue
        for mv in m.moves:
            if d.names.move(mv).upper().replace(" ", "") in wanted:
                keep.add(i)
    if not keep and ids:
        keep.add(0)
    return keep


# --------------------------------------------------------------------------
# acquiring a mother nobody owns: the Safari Zone PIKACHU
#
# PICHU is the one baby whose mother is not already in a box. There is no
# DITTO in Sapphire and the egg's species is the MOTHER's line
# (pret/src/daycare.c:622-645), so a male PIKACHU -- which is what this save
# has -- is worth nothing here: it can only ever father its partner's baby.
# PIKACHU's only wild slots in the whole game are the Safari Zone's two
# southern quadrants at 4% + 1% land, and the Safari hard-codes its own ball
# (pret/src/battle/battle_main.c:5571-5575), so this is 30 Safari Balls and
# 500 steps per 500 entry fee, hunting a 50/50 gender roll on a 5% slot.
# --------------------------------------------------------------------------

#: Entry is a `coord_event`, not a person: stand on it and answer YES
#: (Route121_SafariZoneEntrance/map.json coord_events -> `_EventScript_15C383`).
SAFARI_GATE = "Route121_SafariZoneEntrance"
SAFARI_TRIGGER = (8, 4)
#: The gate's own warp opens into Southeast, so it costs no crossing; both
#: southern quadrants carry the same PIKACHU rows
#: (docs/gen3/guide/encounters.json, from src/data/wild_encounters.h).
SAFARI_HUNT = "SafariZone_Southeast"
#: Mothers this script can go and GET, by baby.
ACQUIRE = {"PICHU": "PIKACHU"}


def grass_lane(d):
    """The longest straight run of GRASS on this map, as ``(y, x0, x1)``.

    Encounters are rolled per step taken on a grass metatile
    (`src/wild_encounter.c`), so pacing anything else is free exercise. A
    ledge or a warp inside the run would end the pace early, hence the
    kind check rather than a bare collision test.
    """
    grid = d.nav.grid(d.map_name())
    bodies = {(n["x"], n["y"]) for n in d.live_npcs() if not n["player"]}

    def ok(cell, xy):
        return (cell is not None and cell.collision == 0
                and "grass" in (cell.kind or "") and xy not in bodies)

    best = None
    for y, row in enumerate(grid):
        x = 0
        while x < len(row):
            if not ok(row[x], (x, y)):
                x += 1
                continue
            x0 = x
            while x < len(row) and ok(row[x], (x, y)):
                x += 1
            if best is None or (x - x0) > (best[2] - best[1] + 1):
                best = (y, x0, x - 1)
    if best is None or best[2] - best[1] < 1:
        raise RuntimeError(f"no grass run on {d.map_name()}")
    return best


def safari_enter(d) -> bool:
    """Pay the fee and get inside. Judged on the map, not on the presses."""
    if d.map_name().startswith("SafariZone"):
        return True
    for _ in range(3):
        if d.map_name() == SAFARI_GATE:
            break
        unwedge(d)
        try:
            if not d.flight.flyable_here():
                d.flight.step_outside()
            d.fly_to("LilycoveCity")
        except Exception as exc:            # noqa: BLE001
            log.info("  fly Lilycove: %s", str(exc)[:80])
        try:
            d.travel(SAFARI_GATE, on_battle="fight", budget_s=360)
        except TravelInterrupted:
            d.fight()
            d.advance_scene(40_000)
        except TravelError as exc:
            log.info("  travel gate: %s", str(exc)[:110])
    if d.map_name() != SAFARI_GATE:
        return False
    # The trigger fires on the step INTO (8,4) -- standing on it does nothing
    # (AGENTS.md gotcha 9) -- and the entry box defaults to YES, with the
    # money and POKeBLOCK CASE checks inside the script.
    for _ in range(4):
        if d.map_name().startswith("SafariZone"):
            return True
        if d.pos() != SAFARI_TRIGGER:
            d.goto(*SAFARI_TRIGGER)
        for _ in range(10):
            if d.map_name().startswith("SafariZone"):
                return True
            d.emu.run_sequence("A:6 .:70")
            d.advance_scene(40_000)
        d.step_dir("D")
        d.step_dir("U")
    return d.map_name().startswith("SafariZone")


def wild_foe(d, tries=24):
    """The wild mon on screen, as an object `Bio.gender` can read.

    ``gEnemyParty`` is the obvious source and is the WRONG one: in a Safari
    encounter ``gEnemyPartyCount`` reads 0, so ``state.enemy_party()`` came
    back empty and the first working hunt logged "ran from an unreadable
    encounter" for every single one of 8 encounters -- it would have fled a
    female PIKACHU without ever knowing.

    ``gBattleMons`` is plaintext in battle and the Safari controller zeroes
    only the PLAYER's battler, so the ODD battlers are the wild side (see
    `state.py`'s note on `battle_ready`). Offsets come from the struct's own
    annotations via `cstruct`, and the stride from the symbol's size.
    """
    b = d.state.battle_mon
    stride = d.state._battle_mon_size
    base = d.emu.resolve("gBattleMons") + stride     # battler 1 is the foe
    for _ in range(tries):
        raw = bytes(d.emu.read(base, stride))
        species = int.from_bytes(raw[b["species"]:b["species"] + 2], "little")
        level = raw[b["level"]]
        if species and level:
            personality = int.from_bytes(
                raw[b["personality"]:b["personality"] + 4], "little")
            return SimpleNamespace(species=species, level=level,
                                   personality=personality)
        if not d.state.in_battle():
            return None
        d.settle(240)
    return None


def leave_battle(d, menus, tries=8) -> bool:
    """Get out of a Safari encounter without throwing anything.

    NEVER `advance_scene` here, and never a bare A press while the action
    menu is up. Both cost a run: the Safari box is BALL / POKEBLOCK / GO NEAR
    / RUN with BALL at index 0 (pret/src/battle/battle_controller_safari.c),
    so a blind A THROWS. The first version of this hunt called
    `advance_scene(40_000)` after each encounter and caught an ODDISH with it
    -- filling the party -- while also spending up to 40,000 frames an
    encounter, which is why 1500 seconds bought only 44 of them.

    Text is advanced by `await_action_menu`, which is built for exactly this
    and stops at any question instead of mashing through it
    (pokeagent/battle.py:1308-1320); an A press here is the last resort, and
    only once the action menu is provably not up.
    """
    ns = NamingScreen(d.emu, d.state)
    for _ in range(tries):
        if not d.state.in_battle() and in_field(d) and not d.scene_active():
            return True
        try:
            if ns.is_open():
                ns.accept()
                d.settle(600)
                continue
        except Exception:                   # noqa: BLE001
            pass
        if d.state.in_battle():
            if d.battle.at_action_menu() or d.battle.await_action_menu():
                d.battle.safari_flee()
                d.settle(400)
                continue
        try:
            lo, hi = menus.bounds()
        except Exception:                   # noqa: BLE001
            lo, hi = 0, 0
        if hi - lo == 1 and menus.select_index(1):      # "nickname?" -> NO
            d.settle(600)
            continue
        d.emu.run_sequence("A:4 .:40")
        d.settle(300)
    return not d.state.in_battle()


def safari_decide(d, bio, menus, want) -> str:
    """Act on one Safari encounter; returns what it did, for the log.

    Only a FEMALE of `want` is worth a ball: the egg's species is the
    mother's line (pret/src/daycare.c:622-645), so a male is worthless here
    however rare it is. Everything else is run from immediately, which also
    keeps the encounter cost down -- one visit is 500 steps and about 44
    encounters, and the budget is spent on visits, not on animations.
    """
    foe = wild_foe(d)
    if foe is None:
        leave_battle(d, menus)
        return "ran from an unreadable encounter"
    name = d.names.species(foe.species)
    if foe.species != want:
        leave_battle(d, menus)
        return f"ran from {name}"
    if bio.gender(foe) != bio.FEMALE:
        leave_battle(d, menus)
        return f"ran from {name}M -- the egg takes the MOTHER's line"
    log.info("  FEMALE %s L%s: throwing (%d balls left)",
             name, foe.level, d.state.safari_balls())
    # ONE approach only: `HandleAction_GoNear` trades +4 catch for +4 flee on
    # the first call and less for the same on every later one
    # (pret/src/battle_main.c:5601-5626).
    d.battle.safari_go_near()
    thrown = 0
    while d.state.in_battle() and d.state.safari_balls() > 0:
        if not d.battle.at_action_menu() and not d.battle.await_action_menu():
            break
        if not d.battle.safari_ball():
            break
        thrown += 1
        d.settle(900)
    leave_battle(d, menus)
    return f"threw {thrown} ball(s) at a female {name}"


def female_of(d, bio, species):
    """A female `species` in the party or the boxes, or None."""
    pool = [(None, i, m) for i, m in enumerate(d.state.party())
            if m.species and not m.is_egg] + boxed(d)
    return next((p for p in pool
                 if p[2].species == species
                 and bio.gender(p[2]) == bio.FEMALE), None)


def hunt_female(d, bio, menus, species_name, budget_s=1800.0):
    """Catch a female `species_name` in the Safari Zone.

    Bounded by wall clock rather than by attempts: the zone ejects on its own
    500-step counter or when the 30 balls run out
    (pret/src/safari_zone.c:28,56-86), and re-entry is another 500, so the
    loop just re-enters until it succeeds or the budget is gone. One visit is
    worth about 44 encounters, measured -- so at PIKACHU's 5% land share and
    a 50/50 gender roll, roughly one female per visit in expectation.
    """
    want = species_id(d, species_name)
    deadline = time.time() + budget_s
    seen = entries = 0
    lane = None
    while time.time() < deadline:
        if female_of(d, bio, want):
            return True
        if not d.map_name().startswith("SafariZone"):
            if not safari_enter(d):
                log.info("  could not get into the Safari Zone (at %s)",
                         d.map_name())
                return False
            entries += 1
            lane = None
            log.info("  entry %d: %d balls, %d steps, money %s", entries,
                     d.state.safari_balls(), d.state.safari_steps(),
                     d.state.money())
        if d.map_name() != SAFARI_HUNT:
            lane = None
            try:
                d.travel(SAFARI_HUNT, budget_s=120)
            except TravelInterrupted:
                seen += 1
                log.info("  #%d %s (on the way)", seen,
                         safari_decide(d, bio, menus, want))
                continue
            except TravelError as exc:
                log.info("  travel %s: %s", SAFARI_HUNT, str(exc)[:90])
        if lane is None:
            try:
                lane = grass_lane(d)
            except RuntimeError as exc:
                log.info("  %s", exc)
                return False
            log.info("  grazing %s row %d x%d-%d", d.map_name(), *lane)
        y, x0, x1 = lane
        if d.pos()[1] != y or not (x0 <= d.pos()[0] <= x1):
            try:
                reached = d.goto(x0, y)
            except TravelInterrupted:
                seen += 1
                log.info("  #%d %s (on the way)", seen,
                         safari_decide(d, bio, menus, want))
                continue
            if not reached:
                log.info("  cannot reach the grass lane %s: %s", lane,
                         d.last_goto_reason)
                lane = None
                d.settle(400)
                continue
        frames = (x1 - x0 + 1) * STEP_FRAMES
        for key in ("RIGHT", "LEFT"):
            d.emu.run_sequence(f"{key}:{frames}")
            if d.state.in_battle():
                seen += 1
                log.info("  #%d %s", seen, safari_decide(d, bio, menus, want))
                if female_of(d, bio, want):
                    log.info("  CAUGHT a female %s after %d encounters over "
                             "%d visit(s)", species_name, seen, entries)
                    return True
                break
            if not d.map_name().startswith("SafariZone"):
                log.info("  ejected after %d encounters: %d balls / %d steps "
                         "left", seen, d.state.safari_balls(),
                         d.state.safari_steps())
                break
    log.info("  out of budget: %d encounters over %d visit(s), no female %s",
             seen, entries, species_name)
    return bool(female_of(d, bio, want))

# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--state", required=True)
    ap.add_argument("--baby", required=True, choices=sorted(BABIES))
    ap.add_argument("--egg-budget", type=float, default=900.0,
                    help="seconds of walking allowed waiting for the egg")
    ap.add_argument("--hatch-budget", type=float, default=900.0,
                    help="seconds of walking allowed hatching it")
    ap.add_argument("--no-save", action="store_true")
    ap.add_argument("--hunt-budget", type=float, default=1800.0,
                    help="seconds allowed catching a mother that is not owned "
                         "(only PICHU needs one: a female Safari PIKACHU)")
    ap.add_argument("--no-acquire", action="store_true",
                    help="report a missing mother instead of going to get her")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    baby = BABIES[a.baby]
    d = Driver(a.state)
    if not leave_title(d):
        log.info("REFUSING: still not in the field (cb %s, tasks %s)",
                 d.state.callback_name(), d.state.tasks())
        return 1
    d.advance_scene(40_000)
    unwedge(d)

    slots = cconst.parse_defines(
        str(paths.CONSTANTS / "global.h"))["DAYCARE_MON_COUNT"]
    dc = DayCareRam(d.emu, slots)
    bio = Bio(d)
    target = DexTarget(d.emu, d.names, d.consts, d.nav, spec=d.spec)
    evo = target.evolutions
    menus = Menus(d.emu, d.state)

    baby_id = species_id(d, baby)
    before = target.progress(d.state)
    caught_before, _ = target.dex_flags(d.state)
    log.info("start %s %s | dex %d/%d | %s eggCycles=%d -> %d steps",
             d.map_name(), d.pos(), before["caught_achievable"],
             before["achievable"], baby, bio.egg_cycles(baby_id), HATCH_STEPS)
    log.info("day care holds %s (steps %d/%d, pending %#x)",
             [d.names.species(m.species) for m in dc.occupants()],
             dc.steps(0), dc.steps(1), dc.pending())

    already = target.evolutions.natdex(baby_id) in caught_before
    if already:
        log.info("%s is ALREADY registered -- nothing to do", baby)
        return 0

    # ---- parents ------------------------------------------------------
    # A pair ALREADY in the day care wins: `find_parents` only sees the party
    # and the boxes, so a resumed run would otherwise pick a second pair and
    # then have to evict the first.
    inside = dc.occupants()
    mother = father = None
    if len(inside) == slots and bio.compat(inside[0], inside[1]):
        mum = next((m for m in inside if bio.gender(m) == bio.FEMALE), None)
        if mum is not None and egg_species(evo, mum.species) == baby_id:
            mother = (None, None, mum)
            father = (None, None, next(m for m in inside
                                       if m.personality != mum.personality))
            why = (f"already deposited, compat "
                   f"{bio.compat(inside[0], inside[1])}")
    if mother is None:
        mother, father, why = find_parents(d, bio, evo, baby)
    if mother is None and baby in ACQUIRE and not a.no_acquire:
        # The only mother this run can still GO AND GET.
        log.info("no mother owned: hunting a female %s (%s)",
                 ACQUIRE[baby], why)
        if hunt_female(d, bio, menus, ACQUIRE[baby], budget_s=a.hunt_budget):
            mother, father, why = find_parents(d, bio, evo, baby)
    if mother is None:
        log.info("BLOCKED: %s", why)
        return 2
    keep = {mother[2].personality, father[2].personality}
    strays = [m for m in dc.occupants() if m.personality not in keep]
    log.info("parents: %s | mother %s, father %s%s", why,
             "party/day care" if mother[0] is None
             else f"box {mother[0]}:{mother[1]}",
             "party/day care" if father[0] is None
             else f"box {father[0]}:{father[1]}",
             f" | {len(strays)} stray(s) to evict first" if strays else "")

    incense = INCENSE.get(baby)
    teacher = Teacher(d)
    if incense:
        # CHECK THE POCKET BEFORE ANY MENU IS OPEN: `gBagPockets` is
        # re-pointed while the bag UI is up, so the same read answers
        # differently once a give flow has started.
        ident = teacher._item_id(incense)
        held = {m.held_item for m in d.state.party()} | {
            m.held_item for m in dc.occupants()}
        in_bag = any(iid == ident
                     for _s, iid, _q in teacher.pocket_items(0))
        if not in_bag and ident not in held:
            log.info("BLOCKED: %s needs %s held by a Day Care parent "
                     "(daycare.c:602-622) and it is not in the ITEMS pocket "
                     "nor held by anything -- fetch it from MtPyre_4F (3,11)",
                     baby, incense)
            return 2
        log.info("%s: %s is available (bag=%s)", baby, incense, in_bag)

    # ---- stage the party ----------------------------------------------
    occupants = {m.personality for m in dc.occupants()}
    if not (mother[2].personality in occupants
            and father[2].personality in occupants):
        if not to_pc(d):
            log.info("BLOCKED: no Pokemon Center reachable (at %s)",
                     d.map_name())
            return 1
        log.info("PC at %s %s | party %s", d.map_name(), d.pos(),
                 show_party(d, bio))
        ok, reason = stage_party(d, bio, mother, father,
                                 keep_free=len(strays))
        if not ok:
            log.info("BLOCKED: %s", reason)
            return 1
        log.info("staged party %s", show_party(d, bio))

        if incense:
            # EITHER parent may carry it: `AlterEggSpeciesWithIncenseItem`
            # tests motherItem OR fatherItem (pret/src/daycare.c:602-622).
            # The mother is tried first only because she is the one whose
            # line decides the species anyway.
            for parent in (mother, father):
                if give_held(d, menus, teacher, parent[2].personality,
                             incense):
                    break
            else:
                log.info("BLOCKED: could not put %s on either parent",
                         incense)
                return 1

        if not to_daycare(d):
            log.info("BLOCKED: could not reach %s (at %s)",
                     DAYCARE_MAP, d.map_name())
            return 1
        if strays and not clear_daycare(d, dc, menus, keep):
            log.info("BLOCKED: could not evict %s from the day care",
                     [d.names.species(m.species) for m in dc.occupants()])
            return 1
        for parent in (mother, father):
            if parent[2].personality in {m.personality
                                         for m in dc.occupants()}:
                continue
            if not daycare_store(d, dc, menus, parent[2].personality):
                log.info("BLOCKED: could not deposit %s",
                         d.names.species(parent[2].species))
                return 1

    inside = dc.occupants()
    if len(inside) != slots:
        log.info("BLOCKED: day care holds %d mons, needs %d",
                 len(inside), slots)
        return 1
    score = bio.compat(inside[0], inside[1])
    log.info("day care: %s | compat %d -> ~%d steps per egg",
             [f"{d.names.species(m.species)}{bio.sex(m)}"
              f"{'+' + d.names.item(m.held_item) if m.held_item else ''}"
              for m in inside], score, int(256 * 100 / max(score, 1)))
    if not score:
        log.info("BLOCKED: compatibility 0 -- no egg will EVER be offered")
        return 1
    mum = next((m for m in inside if bio.gender(m) == bio.FEMALE), None)
    if mum is None:
        log.info("BLOCKED: neither day care mon is female")
        return 1
    predicted = egg_species(evo, mum.species)
    if predicted != baby_id:
        log.info("BLOCKED: this pair would lay %s, not %s",
                 d.names.species(predicted), baby)
        return 1
    if incense:
        # LAST GATE BEFORE 2000-ODD STEPS. Without the incense on a DAY CARE
        # parent the egg is silently downgraded -- AZURILL becomes MARILL
        # (pret/src/daycare.c:602-622) -- and nothing says so until it
        # hatches. `StorePokemonInDaycare` copies the whole box struct
        # (:120-144), so a held item survives the deposit and this read is
        # the real thing the engine will look at.
        ident = teacher._item_id(incense)
        if not any(m.held_item == ident for m in inside):
            log.info("BLOCKED: no day care parent holds %s -- the egg would "
                     "hatch as %s, not %s", incense,
                     d.names.species(species_id(d, "MARILL")
                                     if baby == "AZURILL"
                                     else species_id(d, "WOBBUFFET")), baby)
            return 1
        log.info("%s is on a day care parent -- the egg will be %s",
                 incense, baby)

    # ---- walk out an egg ----------------------------------------------
    egg_steps = 0
    if not any(m.is_egg for m in d.state.party()):
        if dc.pending():
            log.info("an egg is already pending")
        else:
            if not to_daycare(d):
                log.info("BLOCKED: could not reach the walking map")
                return 1
            t0 = time.time()
            while not dc.pending():
                took, stop = hoof(d, dc, 256, "for an egg",
                                  budget_s=max(30.0, a.egg_budget
                                               - (time.time() - t0)))
                egg_steps += took
                if stop == "scene":
                    d.advance_scene(40_000)
                    unwedge(d)
                if stop == "budget":
                    break
            if not dc.pending():
                log.info("no egg after %d steps (compat %d, ~%d expected) "
                         "-- out of budget", egg_steps, score,
                         int(256 * 100 / score))
                return 1
            log.info("EGG PENDING after %d real steps (predicted ~%d at "
                     "compat %d)", egg_steps, int(256 * 100 / score), score)

        if not leave_daycare(d):
            log.info("BLOCKED: could not get back onto Route117")
            return 1
        d.sync_grid()
        if not collect_egg(d, dc, menus):
            log.info("BLOCKED: the DAY-CARE MAN would not hand the egg over "
                     "(party %s)", show_party(d, bio))
            return 1

    egg = next((m for m in d.state.party() if m.is_egg), None)
    log.info("egg in the party (cycles left %d) | party %s",
             egg.friendship, show_party(d, bio))

    # ---- hatch it -----------------------------------------------------
    if not to_daycare(d):
        log.info("BLOCKED: could not reach the walking map to hatch")
        return 1
    hatch_steps, stop = hoof(d, dc, HATCH_STEPS, "to hatch",
                             budget_s=a.hatch_budget)
    if stop == "scene" or not any(m.is_egg for m in d.state.party()):
        log.info("hatch scene at %d real steps", hatch_steps)
        if not dismiss_scene(d, menus):
            log.info("REFUSING TO BANK: hatch scene never released "
                     "(cb %s tasks %s)", d.state.callback_name(),
                     d.state.tasks())
            return 1
    d.advance_scene(40_000)
    unwedge(d)

    after = target.progress(d.state)
    caught_after, _ = target.dex_flags(d.state)
    got = target.evolutions.natdex(baby_id) in caught_after
    log.info("RESULT %s registered=%s | dex %d -> %d (/%d) | "
             "egg %d steps, hatch %d steps (predicted %d) | party %s",
             baby, got, before["caught_achievable"],
             after["caught_achievable"], after["achievable"],
             egg_steps, hatch_steps, HATCH_STEPS, show_party(d, bio))
    for n in sorted(caught_after - caught_before):
        log.info("  NEW dex #%d %s", n, d.names.species(
            target.evolutions.species_of_natdex(n) or 0))
    if d.scene_active():
        log.info("REFUSING TO BANK: scene still active (tasks %s)",
                 d.state.tasks())
        return 1
    if not a.no_save:
        d.save(a.state)
        log.info("banked %s", a.state)
    return 0 if got else 1


if __name__ == "__main__":
    raise SystemExit(main())
