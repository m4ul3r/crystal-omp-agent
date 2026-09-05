"""FLY: refuse from data, and steer the region map by reading it.

The three things worth defending here are the three that were expensive to
get right on the live run:

* every refusal is settled BEFORE a button is pressed, because the party popup
  and the fly map are full-screen and a menu left open eats every movement
  input afterwards (AGENTS gotcha 7);
* the destination table is the cartridge's, not ours -- these tests read the
  real ROM through the real symbol table, with no emulator and no savestate,
  which is exactly what the `unit` marker means here;
* the cursor is driven by READING ``cursorPosX``/``cursorPosY``, one step per
  press. A press count would have been wrong in both directions: the fly map
  auto-repeats a long hold, and the party list swallows its first input.
"""

import json

import pytest

from pokeagent import paths
from pokeagent.cconst import Constants
from pokeagent.fishing import enum_values
from pokeagent.flying import Flight, FlyMap, Landing
from pokeagent.symbols import Symbols

pytestmark = pytest.mark.unit

#: The submenu row ids, from the anonymous enum in include/pokemon_menu.h.
POKEMENU = enum_values("include/pokemon_menu.h", "POKEMENU_SUMMARY")


# ---- ROM without an emulator ---------------------------------------------


class RomReader:
    """The slice of :class:`pokeagent.emu.Sapphire` that ROM tables need.

    Reads the cartridge file directly, so a table test needs neither mGBA nor
    a savestate. Anything outside ROM raises rather than silently answering
    zeroes -- a table read that wandered into RAM would otherwise produce a
    confidently wrong landing point, which is the failure mode this project
    cares about most.
    """

    ROM_BASE = 0x08000000

    def __init__(self):
        self.sym = Symbols()
        self._rom = paths.ROM.read_bytes()

    def resolve(self, where):
        if isinstance(where, int):
            return where
        if isinstance(where, tuple):
            base, offset = where
            return self.resolve(base) + offset
        return self.sym.addr(where)

    def read(self, where, n=1):
        offset = self.resolve(where) - self.ROM_BASE
        if offset < 0 or offset + n > len(self._rom):
            raise AssertionError(f"{where!r} is not in ROM")
        return self._rom[offset : offset + n]

    def u8(self, where, i=0):
        return self.read(where, i + 1)[i]

    def u16(self, where):
        return int.from_bytes(self.read(where, 2), "little")


class MapNames:
    """``(group, num) -> map name`` from the file the build itself consumes."""

    def __init__(self):
        groups = json.loads((paths.MAPS / "map_groups.json").read_text())
        self.table = {
            (gi, ni): name
            for gi, group in enumerate(groups["group_order"])
            for ni, name in enumerate(groups[group])
        }

    def map_name(self, group, num):
        return self.table.get((group, num), f"MAP_{group}_{num}")


@pytest.fixture(scope="module")
def consts():
    return Constants()


@pytest.fixture(scope="module")
def fly_map(consts):
    return FlyMap(RomReader(), MapNames(), consts)


# ---- the destination table ------------------------------------------------


#: Where the fly map's cursor has to sit, and where the hop lands, for three
#: real destinations. These are the numbers the live run actually flew on:
#: Fortree -> Slateport -> Dewford, landing on (19, 20) and (2, 11).
REAL_DESTINATIONS = {
    "SlateportCity": ((9, 12), "SlateportCity", "FLAG_VISITED_SLATEPORT_CITY"),
    "DewfordTown": ((3, 16), "DewfordTown", "FLAG_VISITED_DEWFORD_TOWN"),
    "MossdeepCity": ((25, 7), "MossdeepCity", "FLAG_VISITED_MOSSDEEP_CITY"),
    "LavaridgeTown": ((6, 5), "LavaridgeTown", "FLAG_VISITED_LAVARIDGE_TOWN"),
}


@pytest.mark.parametrize("name", sorted(REAL_DESTINATIONS))
def test_a_map_name_resolves_to_the_roms_own_landing_point(fly_map, name):
    cursor, landing_map, flag = REAL_DESTINATIONS[name]
    found = fly_map.find(name)
    assert found is not None, f"{name} has no fly landing point"
    assert found.cursor == cursor
    assert found.map_name == landing_map
    assert found.unlock_flag_name == flag


def test_the_cursor_is_the_rom_entry_plus_the_engines_own_origin(fly_map):
    """``CreateRegionMapCursor`` (src/region_map.c:637-638) offsets the entry
    by MAPCURSOR_X_MIN/Y_MIN, and both come out of region_map.c."""
    for landing in fly_map.landings():
        x, y, _w, _h = fly_map.entry(landing.mapsec)
        assert landing.cursor == (x + fly_map.origin[0], y + fly_map.origin[1])
        assert 1 <= landing.cursor[0] <= fly_map.bounds[0]
        assert 2 <= landing.cursor[1] <= fly_map.bounds[1]


def test_every_town_and_city_is_a_fly_target_plus_the_battle_tower(fly_map):
    """Sixteen towns and cities (``CreateCityTownFlyTargetIcons`` walks them
    against consecutive ``FLAG_VISITED_*`` flags) and the one special area in
    ``sSpecialFlyAreas``."""
    assert len(fly_map.town_mapsecs()) == 16
    assert [sec for _flag, sec in fly_map.special_areas()] == [
        fly_map.mapsec["MAPSEC_BATTLE_TOWER"]
    ]
    consts_of = {l.mapsec_const for l in fly_map.landings()}
    assert "MAPSEC_BATTLE_TOWER" in consts_of
    assert len(consts_of) == 17


def test_a_route_is_not_a_fly_target(fly_map):
    """Route 118 has a region-map entry and a ``sMapHealLocations`` row, and
    is still unreachable by air -- ``sub_80FB758`` answers 1 for it, and the
    A button only acts on 2 and 4."""
    assert fly_map.find("Route118") is None
    assert fly_map.find("MAPSEC_ROUTE_118") is None


def test_the_landing_follows_the_heal_id_not_the_group_num_pair(fly_map):
    """The one place the two halves of ``sMapHealLocations`` disagree.

    Littleroot's row carries the pair MAP_LITTLEROOT_TOWN *and* the heal id
    for Brendan's bedroom, which are different maps. ``sub_80FC69C`` uses the
    pair only when the heal id is HEAL_LOCATION_NONE, so flying follows the
    heal id -- and for Littleroot specifically it overrides even that, with
    the house-door heal location chosen by player gender.
    """
    row = fly_map.map_heal_row(fly_map.mapsec["MAPSEC_LITTLEROOT_TOWN"])
    names = MapNames()
    pair_map = names.map_name(row[0], row[1])
    heal_map = names.map_name(*fly_map.heal_location(row[2]))
    assert pair_map != heal_map, "pick a different example, they now agree"
    assert (pair_map, heal_map) == ("LittlerootTown",
                                    "LittlerootTown_BrendansHouse_2F")
    # And the fly path takes neither of those rows: it takes the switch's.
    assert fly_map.heal_id_for("MAPSEC_LITTLEROOT_TOWN") == fly_map.heal[
        "HEAL_LOCATION_LITTLEROOT_TOWN_BRENDANS_HOUSE"
    ]
    assert fly_map.heal_id_for(
        "MAPSEC_LITTLEROOT_TOWN", female=True
    ) == fly_map.heal["HEAL_LOCATION_LITTLEROOT_TOWN_MAYS_HOUSE"]


def test_ever_grande_splits_on_the_league_flag(fly_map):
    """One cursor cell, two destinations (src/region_map.c:1630-1631)."""
    assert fly_map.heal_id_for(
        "MAPSEC_EVER_GRANDE_CITY", league=True
    ) == fly_map.heal["HEAL_LOCATION_EVER_GRANDE_CITY_POKEMON_LEAGUE"]
    assert fly_map.heal_id_for(
        "MAPSEC_EVER_GRANDE_CITY", league=False
    ) == fly_map.heal["HEAL_LOCATION_EVER_GRANDE_CITY"]


def test_a_stride_is_never_guessed(fly_map):
    """Gotcha 12. ``struct HealLocation`` declares six bytes of fields and the
    array stride is eight, so a transcribed sizeof would read every landing
    point after the first from the wrong offset."""
    assert fly_map.heal_stride == 8
    assert fly_map.entry_stride == 8
    assert fly_map.map_heal_stride == 3


def test_a_destination_answers_to_every_spelling_a_caller_might_use(fly_map):
    for spelling in ("MossdeepCity", "MOSSDEEP_CITY", "mossdeep city",
                     "MAPSEC_MOSSDEEP_CITY"):
        found = fly_map.find(spelling)
        assert found is not None and found.mapsec_const == "MAPSEC_MOSSDEEP_CITY"
    assert fly_map.find("") is None
    assert fly_map.find("Atlantis") is None


# ---- refusals, with nothing pressed ---------------------------------------

FLY = "MOVE_FLY"


class Mon:
    def __init__(self, moves=(), egg=False):
        self.moves = list(moves)
        self.is_egg = egg


class FakeState:
    """Party, flags and gender. Flags answer to a name or a numeric id, the
    way :meth:`pokeagent.state.GameState.flag` does."""

    def __init__(self, consts, party, held=(), gender="male"):
        self._consts = consts
        self._party = list(party)
        self._held = {
            consts.flags[f] if isinstance(f, str) else f for f in held
        }
        self._gender = gender

    def party(self):
        return self._party

    def flag(self, flag):
        fid = self._consts.flags[flag] if isinstance(flag, str) else flag
        return fid in self._held

    def gender(self):
        return self._gender


class FakeDriver:
    def __init__(self, map_name, knower):
        self._map_name = map_name
        self._knower = knower

    def map_name(self):
        return self._map_name

    def field_moves(self):
        return {"FLY": self._knower}


class NoButtons:
    """An emulator that fails the test if anything touches it."""

    def run_sequence(self, seq):  # pragma: no cover - the point is never here
        raise AssertionError(f"pressed {seq!r} on a path that must refuse")

    def tick(self, frames=1):  # pragma: no cover
        raise AssertionError("advanced frames on a path that must refuse")


def make_flight(consts, fly_map, *, map_name="FortreeCity", knower="SEA BIRD",
                moves=(FLY,), badges=("FLAG_BADGE06_GET",),
                visited=("FLAG_VISITED_SLATEPORT_CITY",),
                map_type="MAP_TYPE_CITY", gender="male"):
    """A Flight wired to fakes, with the real ROM table and real constants.

    `map_type` is injected over :meth:`Flight.map_type` -- that one method is
    a single ``gMapHeader.mapType`` read and needs a live map header. The
    condition built on top of it, ``flyable_here``, is the real one.
    """
    f = object.__new__(Flight)
    f.last_reason = None
    f.last_detail = ""
    f.consts = consts
    f.map = fly_map
    f.map_types = consts.ns("map_types.h")
    f.pokemenu = POKEMENU
    move_ids = [consts.moves[m] for m in moves]
    f.state = FakeState(
        consts, [Mon(move_ids)], held=tuple(badges) + tuple(visited),
        gender=gender,
    )
    f.d = FakeDriver(map_name, knower)
    f.emu = NoButtons()
    f.map_type = lambda: consts.ns("map_types.h")[map_type]
    return f


def test_no_party_member_knows_fly(consts, fly_map):
    f = make_flight(consts, fly_map, knower=None, moves=())
    assert f.fly_to("SlateportCity") is False
    assert f.last_reason == "no-knower"


def test_field_moves_and_the_party_array_disagreeing_is_still_no_knower(
    consts, fly_map
):
    """`field_moves()` is a snapshot; the party can change under it."""
    f = make_flight(consts, fly_map, knower="GHOST", moves=())
    assert f.fly_to("SlateportCity") is False
    assert f.last_reason == "no-knower"
    assert "no party slot has the move" in f.last_detail


def test_the_badge_the_engine_checks_is_badge_six(consts, fly_map):
    """``FlagGet(FLAG_BADGE01_GET + tFieldMoveId)`` with FLY's field id of 5
    (src/pokemon_menu.c:728). Holding every other badge is not enough."""
    others = [f"FLAG_BADGE0{n}_GET" for n in (1, 2, 3, 4, 5, 7, 8)]
    f = make_flight(consts, fly_map, badges=others)
    assert f.badge_held() is False
    assert f.fly_to("SlateportCity") is False
    assert f.last_reason == "no-badge"

    # Badge six alone is enough, and nothing else is required.
    f = make_flight(consts, fly_map, badges=("FLAG_BADGE06_GET",))
    assert f.badge_held() is True


def test_fly_is_refused_indoors_by_the_engines_own_map_type_test(
    consts, fly_map
):
    """``Overworld_MapTypeAllowsTeleportAndFly`` (src/overworld.c:1088-1097)
    takes ROUTE, TOWN, CITY and type 6, and nothing else."""
    f = make_flight(consts, fly_map, map_name="FortreeCity_Gym",
                    map_type="MAP_TYPE_INDOOR")
    assert f.fly_to("SlateportCity") is False
    assert f.last_reason == "indoors"
    assert "MAP_TYPE_INDOOR" in f.last_detail


@pytest.mark.parametrize(
    "map_type", ["MAP_TYPE_ROUTE", "MAP_TYPE_TOWN", "MAP_TYPE_CITY",
                 "MAP_TYPE_6"]
)
def test_the_four_map_types_that_allow_a_hop(consts, fly_map, map_type):
    f = make_flight(consts, fly_map, map_type=map_type)
    assert f.flyable_here() is True


@pytest.mark.parametrize(
    "map_type", ["MAP_TYPE_INDOOR", "MAP_TYPE_UNDERWATER",
                 "MAP_TYPE_UNDERGROUND", "MAP_TYPE_SECRET_BASE"]
)
def test_the_map_types_that_do_not(consts, fly_map, map_type):
    f = make_flight(consts, fly_map, map_type=map_type)
    assert f.flyable_here() is False


def test_a_place_with_no_landing_point_is_refused_by_name(consts, fly_map):
    f = make_flight(consts, fly_map)
    assert f.fly_to("Route118") is False
    assert f.last_reason == "unknown-destination"
    f = make_flight(consts, fly_map)
    assert f.fly_to("Atlantis") is False
    assert f.last_reason == "unknown-destination"


def test_a_town_the_game_has_not_unlocked_is_refused_by_its_flag(
    consts, fly_map
):
    f = make_flight(consts, fly_map, visited=("FLAG_VISITED_SLATEPORT_CITY",))
    assert f.fly_to("MossdeepCity") is False
    assert f.last_reason == "not-visited"
    assert "FLAG_VISITED_MOSSDEEP_CITY" in f.last_detail


def test_already_being_there_is_success_and_presses_nothing(consts, fly_map):
    f = make_flight(consts, fly_map, map_name="SlateportCity")
    assert f.fly_to("SlateportCity") is True
    assert f.last_reason is None


def test_the_refusals_are_ordered_so_the_reason_is_the_useful_one(
    consts, fly_map
):
    """Indoors outranks an unknown destination: stepping outside is the fix
    either way, and the live run hit exactly this ordering from inside
    Fortree's gym."""
    f = make_flight(consts, fly_map, map_name="FortreeCity_Gym",
                    map_type="MAP_TYPE_INDOOR")
    assert f.fly_to("Atlantis") is False
    assert f.last_reason == "indoors"


# ---- the cursor drivers ---------------------------------------------------

_STEP = {"LEFT": (-1, 0), "RIGHT": (1, 0), "UP": (0, -1), "DOWN": (0, 1)}


class FakeRegionMap:
    """A fly map whose cursor moves exactly one cell per press.

    Measured on the real thing: ``DOWN:3 .:14`` moved the cursor 13,2 -> 13,3
    -> 13,4 and so on, one cell each, because ``_swiopen``
    (src/region_map.c:248-277) applies the queued move four frames later and
    the key is already released by then.
    """

    def __init__(self, start, stuck=False):
        self.x, self.y = start
        self.presses = []
        self.stuck = stuck

    def run_sequence(self, seq):
        self.presses.append(seq)
        if self.stuck:
            return
        dx, dy = _STEP[seq.split(":")[0]]
        self.x += dx
        self.y += dy

    def keys(self):
        return [p.split(":")[0] for p in self.presses]


def cursor_flight(start, stuck=False):
    f = object.__new__(Flight)
    f.last_detail = ""
    f.emu = fake = FakeRegionMap(start, stuck=stuck)
    f.cursor = lambda: (fake.x, fake.y)
    return f, fake


@pytest.mark.parametrize(
    "start,target",
    [
        ((13, 2), (9, 12)),      # the live Fortree -> Slateport hop
        ((9, 12), (3, 16)),      # Slateport -> Dewford
        ((3, 16), (6, 5)),       # Dewford -> Lavaridge
        ((5, 5), (5, 5)),        # already there
        ((1, 2), (28, 16)),      # corner to corner
    ],
)
def test_the_map_cursor_walks_to_the_target_and_stops(start, target):
    f, fake = cursor_flight(start)
    assert f._drive_map_cursor(target) is True
    assert (fake.x, fake.y) == target
    # One press per cell, and not one more: arrival is checked before every
    # press, so the loop cannot overshoot and walk back.
    distance = abs(target[0] - start[0]) + abs(target[1] - start[1])
    assert len(fake.presses) == distance


def test_every_press_moves_toward_the_target():
    start, target = (13, 2), (9, 12)
    f, fake = cursor_flight(start)
    assert f._drive_map_cursor(target) is True
    keys = fake.keys()
    assert keys.count("LEFT") == 4 and keys.count("RIGHT") == 0
    assert keys.count("DOWN") == 10 and keys.count("UP") == 0
    # X first, then Y: one axis at a time keeps each press's expected effect
    # a single cell, which is what makes the read-back check meaningful.
    assert keys == ["LEFT"] * 4 + ["DOWN"] * 10


def test_a_cursor_that_will_not_move_is_refused_not_mashed():
    f, fake = cursor_flight((13, 2), stuck=True)
    assert f._drive_map_cursor((9, 12)) is False
    assert "would not leave" in f.last_detail
    # Three presses is the whole budget for a wedged cursor: one plus the two
    # retries that cover a swallowed draw frame.
    assert len(fake.presses) == 3


def test_an_unreadable_cursor_refuses_before_pressing():
    f, fake = cursor_flight((13, 2))
    f.cursor = lambda: None
    assert f._drive_map_cursor((9, 12)) is False
    assert "not readable" in f.last_detail
    assert fake.presses == []


# ---- the party popup ------------------------------------------------------


class FakePopup:
    """The popup's own list and cursor, plus a press log."""

    def __init__(self, rows, cursor=0, stuck=False):
        self.rows = list(rows)
        self.pos = cursor
        self.presses = []
        self.stuck = stuck

    def run_sequence(self, seq):
        self.presses.append(seq)
        if self.stuck or not seq.startswith(("UP", "DOWN")):
            return
        self.pos = max(0, min(len(self.rows) - 1,
                              self.pos + (1 if seq.startswith("DOWN") else -1)))


def popup_flight(*row_names, cursor=0, stuck=False):
    rows = [POKEMENU[name] for name in row_names]
    f = object.__new__(Flight)
    f.last_detail = ""
    f.pokemenu = POKEMENU
    f.emu = fake = FakePopup(rows, cursor=cursor, stuck=stuck)
    f.popup_rows = lambda: list(fake.rows)
    f.popup_cursor = lambda: fake.pos
    return f, fake


def test_the_fly_row_is_found_wherever_the_engine_put_it():
    """``sub_8089A8C`` builds the popup per mon, so FLY's row moves. On the
    live run SEA BIRD's popup was [SURF, FLY, SUMMARY, SWITCH, ITEM, CANCEL]
    and FLY was row 1; a mon that knew only FLY would have it at row 0."""
    f, fake = popup_flight(
        "POKEMENU_SURF", "POKEMENU_FLY", "POKEMENU_SUMMARY",
        "POKEMENU_SWITCH", "POKEMENU_ITEM", "POKEMENU_CANCEL",
    )
    assert f._choose_fly_row() is True
    assert fake.pos == 1
    assert fake.presses == ["DOWN:4 .:14", "A:4 .:30"]


def test_the_fly_row_at_the_top_of_the_list_needs_no_press_at_all():
    f, fake = popup_flight("POKEMENU_FLY", "POKEMENU_SUMMARY",
                           "POKEMENU_CANCEL")
    assert f._choose_fly_row() is True
    assert fake.presses == ["A:4 .:30"]


def test_a_popup_with_no_fly_row_never_presses_a():
    f, fake = popup_flight("POKEMENU_CUT", "POKEMENU_SUMMARY",
                           "POKEMENU_CANCEL")
    assert f._choose_fly_row() is False
    assert "no FLY" in f.last_detail
    assert fake.presses == []


def test_a_popup_cursor_that_will_not_move_never_presses_a():
    f, fake = popup_flight("POKEMENU_SURF", "POKEMENU_FLY",
                           "POKEMENU_CANCEL", stuck=True)
    assert f._choose_fly_row() is False
    assert "would not leave" in f.last_detail
    assert "A:4 .:30" not in fake.presses


def test_a_landing_labels_itself_for_a_legible_refusal(fly_map):
    landing = fly_map.find("EverGrandeCity")
    assert isinstance(landing, Landing)
    assert landing.label == "EVER GRANDE CITY"
