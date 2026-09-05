"""FLY, the field move that finally makes Hoenn small.

SEA BIRD the PELIPPER learned FLY on badge 6 and the harness still could not
use it, because :meth:`trek.Driver.travel` routes on foot over the warp and
connection graph and nothing else. Three cheap collectibles -- the GOOD ROD on
Route 118, the OLD ROD in Dewford, the POKEBLOCK CASE in Slateport -- and the
whole Safari Zone were each a quarter of an hour of walking away. This module
is the hop.

Nothing here is transcribed. Every number comes out of the ROM or the decomp:

* **which places you may fly to** is ``sub_80FB758``
  (src/region_map.c:692-741): a town or city answers 2 when its
  ``FLAG_VISITED_*`` is set and 3 when it is not, the Battle Tower answers 4
  when its landmark flag is set, and the fly map's A button
  (src/region_map.c:1588-1595) accepts *only* 2 and 4. So "not visited" is a
  flag read, not a guess about story progress.
* **where the cursor has to be** is ``gRegionMapEntries[id].x/y`` plus the
  cursor origin, exactly as ``CreateRegionMapCursor`` computes it
  (src/region_map.c:637-638).
* **where the hop actually lands you** is ``sMapHealLocations[id][2]`` fed
  through ``sHealLocations`` (src/region_map.c:1617-1639 -> src/rom4.c
  ``sub_8053538``), with the engine's own three special cases for Littleroot,
  Ever Grande and the Battle Tower.

Two sources genuinely disagree about the landing map and it matters. Each
``sMapHealLocations`` row carries BOTH a ``(mapGroup, mapNum)`` pair *and* a
heal-location id, and for every flyable town the two name different maps --
Littleroot's pair is MAP_LITTLEROOT_TOWN while its heal id is Brendan's
bedroom. ``sub_80FC69C`` uses the ``(group, num)`` pair only when the heal id
is ``HEAL_LOCATION_NONE``, which is true for routes and false for all sixteen
fly targets. So this module follows the heal id, which is the branch flying
actually takes; the pair is dead data on every path we can reach.

The cursor is REAL memory, not a press count. ``struct RegionMap`` keeps
``cursorPosX``/``cursorPosY`` (include/region_map.h:32-33) and the live
instance is reachable two ways that cross-check each other: ``gRegionMapState``
is a ROM pointer to the EWRAM overlay, and ``gRegionMap`` is the pointer
``sub_80FA904`` sets to the struct being driven. Their difference is the
``regionMap`` member's offset, which is therefore derived rather than counted
off the header.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from . import cconst, cstruct, paths
from .fishing import enum_values
from .state import NUM_TASKS

log = logging.getLogger("pokeagent.fly")


def _norm(text) -> str:
    """``"SlateportCity"``, ``"SLATEPORT_CITY"``, ``"slateport city"`` -> one
    key. Callers name maps in CamelCase and the decomp names them in
    SCREAMING_SNAKE; neither should have to know about the other."""
    return "".join(ch for ch in str(text).upper() if ch.isalnum())


@dataclass(frozen=True, slots=True)
class Landing:
    """One region-map fly target, as the ROM describes it."""

    #: `MAPSEC_*` id, which is the fly map's own identifier for the place.
    mapsec: int
    #: The constant's name, e.g. ``"MAPSEC_SLATEPORT_CITY"``.
    mapsec_const: str
    #: Where the fly map's cursor must sit, in cursor coordinates.
    cursor: tuple[int, int]
    #: The flag that turns the target from class 3 (or 0) into 2 (or 4).
    unlock_flag: int
    #: The flag's name when it has one, for a legible refusal.
    unlock_flag_name: str
    #: The map the engine warps to. NOT always the town: Littleroot lands you
    #: inside a house, because that is where its heal location is.
    map_name: str

    @property
    def label(self) -> str:
        return self.mapsec_const.removeprefix("MAPSEC_").replace("_", " ")

    @property
    def keys(self) -> tuple[str, ...]:
        """Every spelling of this destination a caller may reasonably use."""
        return tuple(
            dict.fromkeys(
                (
                    _norm(self.mapsec_const.removeprefix("MAPSEC_")),
                    _norm(self.mapsec_const),
                    _norm(self.map_name),
                )
            )
        )


class FlyMap:
    """The region map's fly-target table, read out of the cartridge.

    Construction touches ROM only, so this is answerable with no menu open and
    no button pressed -- which is the whole point: every :class:`Flight`
    refusal is decided from here before the START menu is even opened.
    """

    def __init__(self, emu, names, consts):
        self.emu = emu
        self.names = names
        self.consts = consts

        self.mapsec = enum_values(
            "include/constants/region_map_sections.h", "MAPSEC_LITTLEROOT_TOWN"
        )
        self.heal = enum_values(
            "include/constants/heal_locations.h", "HEAL_LOCATION_NONE"
        )

        # Gotcha 12: a stride comes from the symbol's own size divided by the
        # element count the decomp declares, and a non-integral division is
        # refused rather than rounded. gRegionMapEntries is declared with
        # designated initialisers up to MAPSEC_DYNAMIC and no further.
        self.entry_stride = self._stride(
            "gRegionMapEntries", self.mapsec["MAPSEC_DYNAMIC"] + 1
        )
        self.heal_stride = self._stride(
            "sHealLocations", self.heal["NUM_HEAL_LOCATIONS"] - 1
        )
        self.map_heal_stride = self._stride(
            "sMapHealLocations", self.mapsec["MAPSEC_ROUTE_134"] + 1
        )

        #: `CreateRegionMapCursor` (src/region_map.c:637-638) places the cursor
        #: at the entry's x/y plus this origin, so the origin is scraped from
        #: the region map's own source rather than written down here.
        defines = cconst.parse_defines(str(paths.PRET / "src" / "region_map.c"))
        self.origin = (defines["MAPCURSOR_X_MIN"], defines["MAPCURSOR_Y_MIN"])
        self.bounds = (defines["MAPCURSOR_X_MAX"], defines["MAPCURSOR_Y_MAX"])

    # ---- ROM tables ------------------------------------------------------

    def _stride(self, symbol, count) -> int:
        size = self.emu.sym.size(symbol)
        if not size or count <= 0 or size % count:
            raise ValueError(
                f"{symbol} is {size:#x} bytes, which is not {count} whole "
                f"entries -- refusing to guess a stride"
            )
        return size // count

    def entry(self, mapsec) -> tuple[int, int, int, int]:
        """``gRegionMapEntries[mapsec]`` as ``(x, y, width, height)``."""
        raw = self.emu.read(
            ("gRegionMapEntries", mapsec * self.entry_stride), 4
        )
        return raw[0], raw[1], raw[2], raw[3]

    def heal_location(self, heal_id) -> tuple[int, int] | None:
        """``sHealLocations[id - 1]`` as ``(mapGroup, mapNum)``.

        ``GetHealLocation`` (src/heal_location.c:29-37) is one-based and
        answers NULL for ``HEAL_LOCATION_NONE`` and for anything past the end,
        so both are answered the same way here.
        """
        count = self.heal["NUM_HEAL_LOCATIONS"] - 1
        if not 1 <= heal_id <= count:
            return None
        raw = self.emu.read(
            ("sHealLocations", (heal_id - 1) * self.heal_stride), 2
        )
        return raw[0], raw[1]

    def map_heal_row(self, mapsec) -> tuple[int, int, int] | None:
        """``sMapHealLocations[mapsec]`` as ``(mapGroup, mapNum, healId)``."""
        count = self.mapsec["MAPSEC_ROUTE_134"] + 1
        if not 0 <= mapsec < count:
            return None
        raw = self.emu.read(
            ("sMapHealLocations", mapsec * self.map_heal_stride), 3
        )
        return raw[0], raw[1], raw[2]

    def special_areas(self) -> list[tuple[int, int]]:
        """``sSpecialFlyAreas`` as ``[(flag, mapsec)]``.

        The table (src/region_map.c:1243-1248) is ``{flag, mapSectionId}``
        pairs terminated by ``MAPSEC_NONE``; only the Battle Tower is in it.
        """
        base = "sSpecialFlyAreas"
        size = self.emu.sym.size(base) or 0
        out = []
        for i in range(size // 4):
            flag = self.emu.u16((base, i * 4))
            sec = self.emu.u16((base, i * 4 + 2))
            if sec == self.mapsec["MAPSEC_NONE"]:
                break
            out.append((flag, sec))
        return out

    # ---- the towns and cities -------------------------------------------

    def town_mapsecs(self) -> list[str]:
        """The MAPSEC constants that have a ``FLAG_VISITED_*`` twin.

        ``CreateCityTownFlyTargetIcons`` (src/region_map.c:1468-1503) walks
        the first sixteen map sections against ``FLAG_VISITED_LITTLEROOT_TOWN``
        incremented once per section, and ``sub_80FB758`` names the same
        sixteen flags one case at a time. Deriving the set from flags.h and
        then CHECKING it against the engine's contiguous-flag assumption means
        neither the count nor the order is written down here.
        """
        flags = self.consts.flags
        first = flags["FLAG_VISITED_LITTLEROOT_TOWN"]
        out = []
        for const, sec in sorted(self.mapsec.items(), key=lambda kv: kv[1]):
            if not const.startswith("MAPSEC_"):
                continue
            twin = "FLAG_VISITED_" + const.removeprefix("MAPSEC_")
            if twin not in flags:
                continue
            if flags[twin] != first + sec:
                raise ValueError(
                    f"{twin} is {flags[twin]:#x} but "
                    f"CreateCityTownFlyTargetIcons expects "
                    f"{first + sec:#x} -- flags.h and region_map.c disagree "
                    f"about which flag unlocks {const}"
                )
            out.append(const)
        if not out:
            raise ValueError("no MAPSEC has a FLAG_VISITED_ twin in flags.h")
        return out

    #: The three fly targets whose landing point is NOT
    #: ``sMapHealLocations[id][2]``, straight off the switch in
    #: ``sub_80FC69C`` (src/region_map.c:1617-1639). Values are the heal
    #: locations the engine passes to ``sub_8053538``; a tuple means the
    #: choice depends on live save data and is resolved in `landing_map`.
    SPECIAL_LANDINGS = {
        "MAPSEC_LITTLEROOT_TOWN": (
            "HEAL_LOCATION_LITTLEROOT_TOWN_BRENDANS_HOUSE",
            "HEAL_LOCATION_LITTLEROOT_TOWN_MAYS_HOUSE",
        ),
        "MAPSEC_EVER_GRANDE_CITY": (
            "HEAL_LOCATION_EVER_GRANDE_CITY_POKEMON_LEAGUE",
            "HEAL_LOCATION_EVER_GRANDE_CITY",
        ),
        "MAPSEC_BATTLE_TOWER": ("HEAL_LOCATION_BATTLE_TOWER_OUTSIDE",),
        "MAPSEC_SOUTHERN_ISLAND": ("HEAL_LOCATION_SOUTHERN_ISLAND_EXTERIOR",),
    }

    def heal_id_for(self, mapsec_const, *, female=False, league=False) -> int:
        """Which heal location this destination warps to.

        `female` is ``gSaveBlock2.playerGender``: Littleroot lands you in your
        OWN bedroom, so the map depends on who you are playing.
        `league` is ``FLAG_SYS_POKEMON_LEAGUE_FLY``, which splits Ever Grande
        into two halves.
        """
        special = self.SPECIAL_LANDINGS.get(mapsec_const)
        if special is not None:
            if mapsec_const == "MAPSEC_LITTLEROOT_TOWN":
                return self.heal[special[1 if female else 0]]
            if mapsec_const == "MAPSEC_EVER_GRANDE_CITY":
                return self.heal[special[0 if league else 1]]
            return self.heal[special[0]]
        row = self.map_heal_row(self.mapsec[mapsec_const])
        return row[2] if row else self.heal["HEAL_LOCATION_NONE"]

    def landing_map(self, mapsec_const, *, female=False, league=False) -> str:
        """The map name a hop to this destination ends on."""
        heal_id = self.heal_id_for(
            mapsec_const, female=female, league=league
        )
        where = self.heal_location(heal_id)
        if where is None:
            # Only reachable for a MAPSEC whose row carries
            # HEAL_LOCATION_NONE, i.e. a route. Routes are never class 2 or 4
            # so they are never confirmable, but answer honestly anyway.
            row = self.map_heal_row(self.mapsec[mapsec_const])
            if row is None:
                return ""
            where = (row[0], row[1])
        return self.names.map_name(*where)

    def landings(self, *, female=False, league=False) -> list[Landing]:
        """Every fly target the region map can draw, visited or not."""
        flags = self.consts.flags
        out = []
        for const in self.town_mapsecs():
            sec = self.mapsec[const]
            x, y, _w, _h = self.entry(sec)
            twin = "FLAG_VISITED_" + const.removeprefix("MAPSEC_")
            out.append(
                Landing(
                    mapsec=sec,
                    mapsec_const=const,
                    cursor=(x + self.origin[0], y + self.origin[1]),
                    unlock_flag=flags[twin],
                    unlock_flag_name=twin,
                    map_name=self.landing_map(
                        const, female=female, league=league
                    ),
                )
            )
        by_id = {sec: const for const, sec in self.mapsec.items()}
        named = self.consts.inverse("flags.h", "FLAG_")
        for flag, sec in self.special_areas():
            const = by_id.get(sec)
            if const is None or const not in self.SPECIAL_LANDINGS:
                continue
            x, y, _w, _h = self.entry(sec)
            out.append(
                Landing(
                    mapsec=sec,
                    mapsec_const=const,
                    cursor=(x + self.origin[0], y + self.origin[1]),
                    unlock_flag=flag,
                    unlock_flag_name=named.get(flag, f"flag {flag:#x}"),
                    map_name=self.landing_map(const),
                )
            )
        return out

    def find(self, destination, *, female=False, league=False) -> Landing | None:
        """A landing point by map name, town name or MAPSEC constant."""
        want = _norm(destination)
        if not want:
            return None
        for landing in self.landings(female=female, league=league):
            if want in landing.keys:
                return landing
        return None


class Flight:
    """One hop, driven through the engine's own cursors.

    Refusals happen BEFORE any button is pressed, because a wrong press on
    this path is expensive: the party popup and the region map are both
    full-screen, and a menu left open eats every movement input afterwards
    (AGENTS gotcha 7 / #17). Every failure path closes what it opened.
    """

    #: The classes of ``sub_80FB758`` that the fly map's A button acts on
    #: (src/region_map.c:1588-1595). 2 is a visited town or city, 4 an
    #: unlocked special area; on 3 (never visited) and 0 the engine ignores A
    #: entirely, so pressing it there is a silent no-op, not an error.
    CONFIRMABLE = (2, 4)

    #: Map types ``Overworld_MapTypeAllowsTeleportAndFly`` accepts
    #: (src/overworld.c:1088-1097). This is the engine's own condition; there
    #: is no list of indoor maps anywhere in it.
    FLYABLE_MAP_TYPES = ("MAP_TYPE_ROUTE", "MAP_TYPE_TOWN", "MAP_TYPE_6",
                         "MAP_TYPE_CITY")

    #: The overworld party list's input task, and the popup that appears when
    #: you press A on a member (src/pokemon_menu.c:253-306). Both handlers
    #: open with `if (!gPaletteFade.active)`, so the task existing is NOT the
    #: same thing as the screen accepting a press.
    PARTY_LIST_TASK = "HandleDefaultPartyMenu"
    PARTY_POPUP_TASK = "sub_8089D94"

    #: ``gMain.callback2`` once the party screen is built. `CB2_InitPartyMenu`
    #: runs first and every press during it is discarded -- measured: the
    #: party list task was already up, the cursor read 0, and six DOWN presses
    #: moved nothing, which the cursor loop correctly refused as stuck.
    PARTY_MAIN_CB2 = "CB2_PartyMenuMain"

    #: The overworld callback. `_back_out` presses B until this is back,
    #: because "no task I recognise" is not "the field has input".
    FIELD_CB2 = "CB2_Overworld"

    #: ``gMain.callback2`` while the fly map owns the screen, and the region
    #: map's own sub-callback that reads the d-pad
    #: (src/region_map.c:1559-1603). Both are checked: the fade-in callback
    #: `sub_80FC5B4` discards every press.
    REGION_MAP_CB2 = "CB2_FlyRegionMap"
    REGION_MAP_INPUT = "sub_80FC600"

    def __init__(self, driver):
        self.d = driver
        self.emu = driver.emu
        self.state = driver.state
        self.names = driver.names
        self.consts = driver.consts
        self.last_reason: str | None = None
        self.last_detail = ""

        self.map = FlyMap(self.emu, self.names, self.consts)
        self.map_header = cstruct.layout("MapHeader", "global.fieldmap.h")
        self.region_map = cstruct.layout("RegionMap", "region_map.h")
        self.task = cstruct.layout("Task", "task.h")
        self.sprite_data = cstruct.layout("Sprite", "sprite.h")["data"]
        self.task_stride = self.map._stride("gTasks", NUM_TASKS)
        self.sprite_stride = self.map._stride(
            "gSprites",
            cconst.parse_defines(str(paths.INCLUDE / "sprite.h"))["MAX_SPRITES"]
            + 1,
        )
        self.pokemenu = enum_values(
            "include/pokemon_menu.h", "POKEMENU_SUMMARY"
        )
        self.menu_action_pokemon = enum_values(
            "src/start_menu.c", "MENU_ACTION_POKEDEX"
        )["MENU_ACTION_POKEMON"]
        self.map_types = self.consts.ns("map_types.h")

    def _fail(self, why, detail="") -> bool:
        self.last_reason = why
        self.last_detail = detail
        log.info("fly: %s%s", why, f" -- {detail}" if detail else "")
        return False

    # ---- data, no buttons -------------------------------------------------

    @property
    def fly_move_id(self) -> int:
        return self.consts.moves["MOVE_FLY"]

    def knower(self) -> int | None:
        """Party slot of the first member that knows FLY, or None."""
        for index, mon in enumerate(self.state.party()):
            if getattr(mon, "is_egg", False):
                continue
            if self.fly_move_id in tuple(mon.moves):
                return index
        return None

    def badge_held(self) -> bool:
        """The badge the engine itself checks for FLY.

        ``PokemonMenu_FieldMove`` gates every field move on
        ``FlagGet(FLAG_BADGE01_GET + tFieldMoveId)``
        (src/pokemon_menu.c:728), where ``tFieldMoveId`` is the row's
        ``POKEMENU_*`` id minus ``POKEMENU_FIRST_FIELD_MOVE_ID``. FLY's is 5,
        so the flag is BADGE06 -- computed here the same way, so a decomp that
        reorders the submenu cannot silently break it.
        """
        field_id = (
            self.pokemenu["POKEMENU_FLY"]
            - cconst.parse_defines(str(paths.INCLUDE / "pokemon_menu.h"))[
                "POKEMENU_FIRST_FIELD_MOVE_ID"
            ]
        )
        return bool(self.state.flag(self.consts.flags["FLAG_BADGE01_GET"]
                                   + field_id))

    def map_type(self) -> int:
        return self.emu.u8(
            self.emu.resolve("gMapHeader") + self.map_header["mapType"]
        )

    def flyable_here(self) -> bool:
        """``Overworld_MapTypeAllowsTeleportAndFly(gMapHeader.mapType)``."""
        allowed = {self.map_types[name] for name in self.FLYABLE_MAP_TYPES}
        return self.map_type() in allowed

    def _female(self) -> bool:
        return self.state.gender() == "female"

    def _league(self) -> bool:
        try:
            return bool(self.state.flag("FLAG_SYS_POKEMON_LEAGUE_FLY"))
        except Exception:  # noqa: BLE001 - an unknown flag is simply not set
            return False

    def destinations(self) -> list[Landing]:
        """Every fly target, with the ones you may actually reach first."""
        landings = self.map.landings(
            female=self._female(), league=self._league()
        )
        return sorted(
            landings, key=lambda l: (not self.unlocked(l), l.mapsec)
        )

    def unlocked(self, landing: Landing) -> bool:
        return bool(self.state.flag(landing.unlock_flag))

    def find(self, destination) -> Landing | None:
        return self.map.find(
            destination, female=self._female(), league=self._league()
        )

    # ---- the live region map ---------------------------------------------

    def _region_map_base(self) -> int | None:
        """Address of the ``struct RegionMap`` the engine is driving.

        ``gRegionMap`` is the pointer ``sub_80FA904`` sets
        (src/region_map.c:129-135) and it is NOT cleared when the map closes,
        so it is only trusted while the fly map owns the screen. Cross-checked
        against ``gRegionMapState``, the ROM pointer to the EWRAM overlay: the
        difference between them is the ``regionMap`` member's offset, which is
        how that offset is obtained instead of counting bytes in
        include/region_map.h.
        """
        base = self.emu.u32("gRegionMap")
        state = self.emu.u32("gRegionMapState")
        if not base or not state:
            return None
        offset = base - state
        if not 0 <= offset < 0x100:
            log.warning(
                "gRegionMap %#x is not inside gRegionMapState %#x -- "
                "refusing to read a cursor out of it", base, state
            )
            return None
        return base

    def cursor(self) -> tuple[int, int] | None:
        """``(cursorPosX, cursorPosY)`` from the live region map."""
        base = self._region_map_base()
        if base is None:
            return None
        return (
            self.emu.u16(base + self.region_map["cursorPosX"]),
            self.emu.u16(base + self.region_map["cursorPosY"]),
        )

    def selected(self) -> tuple[int, int] | None:
        """``(mapSectionId, unk16)``: what the cursor is on and its class."""
        base = self._region_map_base()
        if base is None:
            return None
        return (
            self.emu.u16(base + self.region_map["mapSectionId"]),
            self.emu.u8(base + self.region_map["unk16"]),
        )

    def ever_grande_area(self) -> int:
        base = self._region_map_base()
        if base is None:
            return 0
        return self.emu.u8(base + self.region_map["everGrandeCityArea"])

    def _region_map_callback(self) -> str:
        """Which of the fly map's own sub-callbacks is running.

        ``gRegionMapState->callback`` is the first member of the overlay, so
        the ROM pointer ``gRegionMapState`` addresses it directly. The name
        matters because ``sub_80FC5B4`` is a fade and swallows every press.
        """
        ptr = self.emu.u32(self.emu.u32("gRegionMapState")) & ~1
        sym = self.emu.sym.at(ptr)
        return sym.name if sym else f"{ptr:#010x}"

    def at_fly_map(self) -> bool:
        """The fly map is up AND will act on a press this frame."""
        try:
            if self.state.callback_name() != self.REGION_MAP_CB2:
                return False
            if self._region_map_callback() != self.REGION_MAP_INPUT:
                return False
        except Exception:  # noqa: BLE001 - a torn read is simply "not yet"
            return False
        return self._region_map_base() is not None

    # ---- the party list and its popup ------------------------------------

    def _task_addr(self, name) -> int | None:
        base = self.emu.resolve("gTasks")
        for i in range(NUM_TASKS):
            addr = base + i * self.task_stride
            if not self.emu.u8(addr + self.task["isActive"]):
                continue
            ptr = self.emu.u32(addr + self.task["func"]) & ~1
            sym = self.emu.sym.at(ptr)
            if sym and sym.name == name:
                return addr
        return None

    def party_cursor(self) -> int | None:
        """Which party slot the list's cursor is on.

        The engine reads it as
        ``gSprites[gTasks[task].data[3] >> 8].data[0]``
        (``sub_806CA00``/``sub_806CA38``, src/party_menu.c), which is the
        number ``HandleDefaultPartyMenu`` acts on when A is pressed. Reading
        the same expression is the difference between steering and hoping --
        the run already lost a team rotation to a hardcoded sprite offset.
        """
        addr = self._task_addr(self.PARTY_LIST_TASK)
        if addr is None:
            return None
        data3 = self.emu.u16(addr + self.task["data"] + 3 * 2)
        sprite = (
            self.emu.resolve("gSprites")
            + (data3 >> 8) * self.sprite_stride
            + self.sprite_data
        )
        return self.emu.s16(sprite)

    def popup_rows(self) -> list[int]:
        """The popup's rows, as the engine built them for THIS mon.

        ``sub_8089A8C`` (src/pokemon_menu.c:192-230) appends one row per field
        move the mon knows and then SUMMARY / SWITCH / ITEM / CANCEL, so the
        row FLY sits on is different for every party member. Reading the list
        is the only way to know it.
        """
        count = self.emu.u8("sPokeMenuOptionsNo")
        size = self.emu.sym.size("sPokeMenuOptionsOrder") or 8
        raw = bytes(self.emu.read("sPokeMenuOptionsOrder", size))
        return list(raw[: min(count, size)])

    def popup_cursor(self) -> int:
        """``sPokeMenuCursorPos``, which is the index ``sub_8089D94`` uses to
        pick a row out of ``sPokeMenuOptionsOrder``."""
        return self.emu.u8("sPokeMenuCursorPos")

    def fading(self) -> bool:
        """``gPaletteFade.active``. Every press that lands during a fade is
        discarded by all three handlers on this path."""
        return bool(self.emu.u8(self.emu.resolve("gPaletteFade") + 7) & 0x80)

    def _party_screen_ready(self) -> bool:
        """The party screen is BUILT, not merely opening."""
        return (
            not self.fading()
            and self.state.callback_name() == self.PARTY_MAIN_CB2
        )

    def at_popup(self) -> bool:
        return (
            self._party_screen_ready()
            and self._task_addr(self.PARTY_POPUP_TASK) is not None
        )

    def at_party_list(self) -> bool:
        return (
            self._party_screen_ready()
            and self._task_addr(self.PARTY_LIST_TASK) is not None
        )

    def on_menu(self) -> bool:
        """Anything other than the field owns the screen.

        Deliberately looser than the two predicates above: `_back_out` must
        keep pressing B through a fade, and a fade is exactly when the strict
        readiness tests read False.
        """
        try:
            return (self.state.callback_name() != self.FIELD_CB2
                    or self.d.scene_active())
        except Exception:  # noqa: BLE001
            return True

    # ---- driving ----------------------------------------------------------

    def _wait(self, predicate, frames=900, step=6) -> bool:
        """Advance until `predicate` holds. Presses NOTHING."""
        spent = 0
        while spent < frames:
            if predicate():
                return True
            self.emu.tick(step)
            spent += step
        return bool(predicate())

    def _open_party(self) -> bool:
        """START -> POKEMON, using the list the engine built for this save.

        ``BuildStartMenuActions`` drops POKEDEX before you own it and POKENAV
        before you are given one (src/start_menu.c:262-276), so the row index
        is read out of ``sCurrentStartMenuActions`` rather than assumed. The
        predecessor opened the POKEDEX by assuming row 1 and then every
        further press was eaten by a dex entry.
        """
        from .menus import Menus

        menus = Menus(self.emu, self.state)
        self.emu.run_sequence("START:4 .:30")
        count = self.emu.u8("sNumStartMenuActions")
        size = self.emu.sym.size("sCurrentStartMenuActions") or 10
        actions = list(bytes(self.emu.read("sCurrentStartMenuActions", size)))
        actions = actions[: min(count, size)]
        if self.menu_action_pokemon not in actions:
            return self._fail(
                "no-pokemon-entry",
                f"the START menu built {actions!r} with no POKEMON row",
            )
        if not menus.select_index(actions.index(self.menu_action_pokemon)):
            return self._fail("start-menu", menus.last_reason or "")
        if not self._wait(self.at_party_list, frames=600):
            return self._fail("party-list", "the party list never opened")
        return True

    def _drive_party_cursor(self, slot, max_steps=14) -> bool:
        """Step the party cursor onto `slot`, verifying every press.

        The list swallows its first input while it draws (gotcha 2), so one
        press that changes nothing is retried; two in a row is a stuck menu
        and refused rather than mashed.
        """
        self.last_detail = ""
        stuck = 0
        for _ in range(max_steps):
            here = self.party_cursor()
            if here is None:
                self.last_detail = "the party cursor is not readable"
                return False
            if here == slot:
                return True
            self.emu.run_sequence(
                "DOWN:4 .:18" if here < slot else "UP:4 .:18"
            )
            if self.party_cursor() == here:
                stuck += 1
                if stuck > 1:
                    self.last_detail = (
                        f"the party cursor would not leave slot {here} "
                        f"(wanted {slot})"
                    )
                    return False
            else:
                stuck = 0
        self.last_detail = f"the party cursor never reached slot {slot}"
        return False

    def _choose_fly_row(self, max_steps=12) -> bool:
        """Put the popup's cursor on FLY and press A.

        Verified against ``sPokeMenuOptionsOrder[sPokeMenuCursorPos]`` -- the
        exact expression ``sub_8089D94`` evaluates when A is pressed
        (src/pokemon_menu.c:297) -- so the wrong row can never be confirmed.
        """
        self.last_detail = ""
        fly = self.pokemenu["POKEMENU_FLY"]
        rows = self.popup_rows()
        if fly not in rows:
            self.last_detail = f"the popup rows are {rows!r}, with no FLY"
            return False
        target = rows.index(fly)
        stuck = 0
        for _ in range(max_steps):
            here = self.popup_cursor()
            if here == target:
                break
            self.emu.run_sequence("DOWN:4 .:14" if here < target else "UP:4 .:14")
            if self.popup_cursor() == here:
                stuck += 1
                if stuck > 1:
                    self.last_detail = (
                        f"the popup cursor would not leave row {here} "
                        f"(wanted {target})"
                    )
                    return False
            else:
                stuck = 0
        else:
            self.last_detail = f"the popup cursor never reached row {target}"
            return False
        rows = self.popup_rows()
        here = self.popup_cursor()
        if not (0 <= here < len(rows)) or rows[here] != fly:
            self.last_detail = (
                f"the popup cursor is on row {here} of {rows!r}, not FLY -- "
                "refusing to press A"
            )
            return False
        self.emu.run_sequence("A:4 .:30")
        return True

    def _drive_map_cursor(self, target, max_steps=64) -> bool:
        """Walk the fly map's cursor to `target`, reading it every press.

        One axis at a time and one step per press, because ``_swiopen``
        (src/region_map.c:248-277) applies a queued move four frames after the
        d-pad is seen and re-reads the pad afterwards -- a long hold
        auto-repeats. The cursor is re-read after every press, so an
        auto-repeat that slips through is corrected rather than compounded.
        """
        self.last_detail = ""
        want_x, want_y = target
        stuck = 0
        for _ in range(max_steps):
            here = self.cursor()
            if here is None:
                self.last_detail = "the region map cursor is not readable"
                return False
            if here == (want_x, want_y):
                return True
            if here[0] != want_x:
                key = "RIGHT" if here[0] < want_x else "LEFT"
            else:
                key = "DOWN" if here[1] < want_y else "UP"
            self.emu.run_sequence(f"{key}:3 .:14")
            if self.cursor() == here:
                stuck += 1
                if stuck > 2:
                    self.last_detail = (
                        f"the map cursor would not leave {here} "
                        f"(wanted {(want_x, want_y)})"
                    )
                    return False
            else:
                stuck = 0
        self.last_detail = (
            f"the map cursor never reached {(want_x, want_y)} "
            f"(stopped at {self.cursor()})"
        )
        return False

    def _back_out(self) -> None:
        """Get control back, whatever screen we are on.

        B is the safe key everywhere on this path: it cancels the fly map
        (src/region_map.c:1596-1599), the popup and the party list. Then
        `advance_scene`, because backing out of a full-screen menu leaves the
        overworld mid-fade and a caller that walks immediately gets its input
        eaten -- the same trap `teaching._back_out` is built around.
        """
        for _ in range(12):
            if not self.on_menu():
                break
            self.emu.run_sequence("B:4 .:24")
        self.d.advance_scene(40_000)
        if self.d.scene_active():
            log.warning("fly: still on a menu after backing out")

    # ---- the whole thing --------------------------------------------------

    def step_outside(self, tries=3) -> bool:
        """Walk out the door so FLY becomes legal, and say whether it worked.

        Refusing indoors was CORRECT and it wedged an entire run. The play loop
        healed at LilycoveCity_PokemonCenter_1F and then, still standing on the
        nurse's tile, tried to fly to its next objective:

            fly: indoors -- LilycoveCity_PokemonCenter_1F is MAP_TYPE_INDOOR,
            which Overworld_MapTypeAllowsTeleportAndFly refuses
            could not reach MtPyre_Summit (9): no walkable route from
            LilycoveCity_PokemonCenter_1F

        Fly refused, the map graph could not route out of the building either,
        so the run sat on that tile with its stall recovery firing -- 3,885
        frames in 28 minutes, roughly two frames a second, twice in one night.
        Every part was working: the engine's rule, the honest refusal, the
        stall detector. Nobody opened the door.

        A Centre or a shop has exactly one way out, so this takes the warp that
        lands on a flyable map and re-checks rather than trusting it.
        """
        d = self.d
        for _ in range(tries):
            if self.flyable_here():
                return True
            here = d.map_name()
            try:
                exits = [e for e in d.exits(here) if e.get("kind") == "warp"]
            except Exception:  # noqa: BLE001
                return False
            moved = False
            for e in exits:
                try:
                    if d.take_warp(int(e["x"]), int(e["y"])):
                        moved = d.map_name() != here
                        if moved:
                            break
                except Exception:  # noqa: BLE001 - try the next door
                    continue
            if not moved:
                return False
        return self.flyable_here()

    def fly_to(self, destination, max_frames=40_000) -> bool:
        """Fly to a town, city or unlocked landmark. True only on arrival.

        Every refusal is decided from data, before the START menu opens:

        ``no-knower``   nobody in the party knows FLY
        ``no-badge``    ``FLAG_BADGE01_GET + 5`` is not set
        ``indoors``     ``Overworld_MapTypeAllowsTeleportAndFly`` says no
        ``unknown-destination``  no region-map landing point by that name
        ``not-visited`` the landing point's unlock flag is clear
        """
        self.last_reason = None
        self.last_detail = ""

        if not self.d.field_moves().get("FLY"):
            return self._fail("no-knower", "no party member knows FLY")
        slot = self.knower()
        if slot is None:
            return self._fail(
                "no-knower",
                "field_moves() names a FLY knower but no party slot has the "
                "move -- the party changed underneath us",
            )
        if not self.badge_held():
            return self._fail(
                "no-badge",
                "FlagGet(FLAG_BADGE01_GET + 5) is clear, so "
                "PokemonMenu_FieldMove would answer CAN'T BE USED",
            )
        if not self.flyable_here() and not self.step_outside():
            names = self.consts.inverse("map_types.h", "MAP_TYPE_")
            here = self.map_type()
            return self._fail(
                "indoors",
                f"{self.d.map_name()} is "
                f"{names.get(here, here)}, which "
                f"Overworld_MapTypeAllowsTeleportAndFly refuses",
            )

        landing = self.find(destination)
        if landing is None:
            return self._fail(
                "unknown-destination",
                f"{destination!r} is not a region-map fly target",
            )
        if not self.unlocked(landing):
            return self._fail(
                "not-visited",
                f"{landing.unlock_flag_name} is clear, so the fly map draws "
                f"{landing.label} greyed out and ignores A on it",
            )
        if self.d.map_name() == landing.map_name:
            self.last_detail = f"already on {landing.map_name}"
            return True

        log.info("fly: %s -> %s (%s, cursor %s)", self.d.map_name(),
                 landing.label, landing.map_name, landing.cursor)

        # ---- from here on, buttons ----
        if not self._open_party():
            self._back_out()
            return False
        if not self._drive_party_cursor(slot):
            self._back_out()
            return self._fail("party-cursor", self.last_detail)
        self.emu.run_sequence("A:4 .:36")
        if not self._wait(self.at_popup, frames=600):
            self._back_out()
            return self._fail("no-popup", "the party popup never opened")
        if not self._choose_fly_row():
            self._back_out()
            return self._fail("no-fly-row", self.last_detail)
        if not self._wait(self.at_fly_map, frames=900):
            self._back_out()
            return self._fail(
                "no-region-map",
                f"pressed FLY but the callback is "
                f"{self.state.callback_name()}",
            )
        if not self._drive_map_cursor(landing.cursor):
            self._back_out()
            return self._fail("map-cursor", self.last_detail)

        picked = self.selected()
        if picked is None or picked[0] != landing.mapsec:
            self._back_out()
            return self._fail(
                "wrong-target",
                f"the cursor reached {landing.cursor} but the map reads "
                f"section {picked[0] if picked else '?'}, not "
                f"{landing.mapsec} ({landing.label})",
            )
        if picked[1] not in self.CONFIRMABLE:
            self._back_out()
            return self._fail(
                "not-visited",
                f"sub_80FB758 classes {landing.label} as {picked[1]}, and the "
                f"fly map's A button only acts on {self.CONFIRMABLE}",
            )

        # Ever Grande is two places under one cursor, and which one it is is a
        # live read (src/region_map.c:1630-1631), so the expected map is
        # settled now rather than at table-build time.
        expected = landing.map_name
        if landing.mapsec_const == "MAPSEC_EVER_GRANDE_CITY":
            expected = self.map.landing_map(
                landing.mapsec_const,
                league=self._league() and self.ever_grande_area() == 0,
            )

        self.emu.run_sequence("A:4 .:40")
        arrived = self._wait(
            lambda: self.d.map_name() == expected, frames=max_frames, step=12
        )
        if not arrived:
            self._back_out()
            return self._fail(
                "no-arrival",
                f"confirmed {landing.label} but the map is "
                f"{self.d.map_name()}, not {expected}",
            )
        self.d.settle(600)
        self.d.advance_scene(40_000)
        self.d.settle(300)
        log.info("fly: landed on %s at %s", self.d.map_name(), self.d.pos())
        return True
