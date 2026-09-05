"""Value-assert the item-source parser against the decompilation.

Same doctrine as ``test_parser_values.py``: every assertion names the
``pret/`` file and line it is quoting, so a decomp bump that moves the data
fails here with a citation instead of failing live with a wrong coordinate.

The Crystal module this ports was written after a Champion run finished
without HM02 FLY. The regression that matters most is therefore the crudest
one: all eight HMs must appear in the table, and FLY must resolve to a
coordinate five caller hops from its give line.

Live flag reads use a duck-typed fake state rather than a savestate, because
the interesting cases are "flag set" and "flag clear" for the same source and
no pair of checkpoints differs by exactly one flag. The ROM-resident halves
(item importance, TMHMMoves) use the real ``emu``/``names`` fixtures.
"""

import pytest

from pokeagent import missables as M

pytestmark = pytest.mark.unit

#: Kind and macro histogram of the parse. Pinned so a regex that stops
#: matching one macro form -- the exact way the Crystal parser lost its
#: item balls -- shows up as a number instead of a silently shorter list.
EXPECTED_KINDS = {"ball": 136, "hidden": 98, "npc": 111, "script": 22}
EXPECTED_MACROS = {"finditem": 136, "giveitem": 128, "additem": 5, "": 98}


class FakeState:
    """The surface :mod:`missables` reads: flags, bag, party, tables."""

    def __init__(self, emu, names, consts, set_flags=(), bag=(), party=()):
        self.emu = emu
        self.names = names
        self.consts = consts
        self._set = set(set_flags)
        self._bag = dict(bag)
        self._party = list(party)

    def flag(self, flag):
        return flag in self._set

    def bag(self):
        return {"items": self._bag}

    def party(self):
        return list(self._party)


class FakeMon:
    def __init__(self, nickname, moves, species=0):
        self.nickname = nickname
        self.moves = tuple(moves)
        self.species = species


@pytest.fixture(scope="module")
def sources():
    return M.parse_item_sources()


def _one(sources, item, **match):
    rows = [
        s
        for s in sources
        if s.item == item and all(getattr(s, k) == v for k, v in match.items())
    ]
    assert len(rows) == 1, f"expected exactly one {item} matching {match}, got {rows}"
    return rows[0]


# ---- the parse -------------------------------------------------------


def test_route102_potion_ball(sources):
    """The first item ball a player can reach.

    `data/item_ball_scripts.inc:1-3`  Route102_EventScript_1B1439: finditem
        ITEM_POTION
    `data/maps/Route102/map.json`     OBJ_EVENT_GFX_ITEM_BALL at (11,15),
        script Route102_EventScript_1B1439, flag FLAG_ITEM_ROUTE102_1

    The give line lives in a file with no map in its path at all, so this
    row existing at all proves the object_event lookup, not just the regex.
    """
    ball = _one(sources, "ITEM_POTION", map="Route102")
    assert (ball.kind, ball.x, ball.y) == ("ball", 11, 15)
    assert ball.flag == "FLAG_ITEM_ROUTE102_1"
    assert ball.script == "Route102_EventScript_1B1439"
    assert ball.source_line == "data/item_ball_scripts.inc:2"
    assert ball.macro == "finditem"
    assert ball.unresolved is None


def test_cut_is_an_npc_gift(sources):
    """`data/maps/RustboroCity_CuttersHouse/scripts.inc:7-10`

        goto_if_set FLAG_RECEIVED_HM01, ..._EventScript_ExplainCut
        giveitem ITEM_HM01_CUT
        setflag FLAG_RECEIVED_HM01

    Object at (7,5) in that map's `map.json`.
    """
    cut = _one(sources, "ITEM_HM01_CUT")
    assert (cut.kind, cut.map, cut.x, cut.y) == (
        "npc",
        "RustboroCity_CuttersHouse",
        7,
        5,
    )
    assert cut.flag == "FLAG_RECEIVED_HM01"
    assert cut.source_line == "data/maps/RustboroCity_CuttersHouse/scripts.inc:9"
    assert cut.hops == 0, "the give sits directly in the object's own script"


def test_fly_resolves_five_caller_hops_up(sources):
    """The regression the whole module exists for.

    `data/maps/Route119/scripts.inc:150`  giveitem ITEM_HM02_FLY, inside
    Route119_EventScript_151352 -- which is `call`ed, not attached to
    anything. The chain back to a coordinate is

        151352 <- 1512BD:114 <- 15128D:99 <- 15125E:92 <- 1511DB:75
                <- 1511C5, the coord_event at (25,31) in Route119/map.json

    i.e. five hops. A one-hop parser reports FLY as unplaceable, which is
    indistinguishable from not reporting it at all.
    """
    fly = _one(sources, "ITEM_HM02_FLY")
    assert (fly.map, fly.x, fly.y) == ("Route119", 25, 31)
    assert fly.kind == "script", "entered from a coord_event, not an NPC"
    assert fly.flag == "FLAG_RECEIVED_HM02"
    assert fly.source_line == "data/maps/Route119/scripts.inc:150"
    assert fly.hops == 5
    assert fly.unresolved is None


def test_waterfall_and_dive_are_item_balls(sources):
    """Two HMs are ground items, so a gift-only parser misses both.

    `data/item_ball_scripts.inc:478`  finditem ITEM_HM07_WATERFALL
    `data/maps/CaveOfOrigin_B3F/map.json`  ITEM_BALL at (6,5),
        flag FLAG_ITEM_CAVE_OF_ORIGIN_B3F_1
    `data/item_ball_scripts.inc:538`  finditem ITEM_HM08_DIVE
    `data/maps/VictoryRoad_B2F/map.json`  ITEM_BALL at (13,8)
    """
    falls = _one(sources, "ITEM_HM07_WATERFALL")
    assert (falls.kind, falls.map, falls.x, falls.y) == (
        "ball",
        "CaveOfOrigin_B3F",
        6,
        5,
    )
    assert falls.flag == "FLAG_ITEM_CAVE_OF_ORIGIN_B3F_1"

    dive = _one(sources, "ITEM_HM08_DIVE", map="VictoryRoad_B2F")
    assert (dive.kind, dive.x, dive.y) == ("ball", 13, 8)


def test_dive_has_two_real_sources(sources):
    """Steven's gift and a fallback ball in the same house, one flag each.

    `data/maps/MossdeepCity_StevensHouse/scripts.inc:41`  giveitem, guarded
        by FLAG_RECEIVED_HM08 (line 42), entered from the OnFrame table
        (line 26) so it has no coordinates
    `data/maps/MossdeepCity_StevensHouse/scripts.inc:126`  finditem, the ball
        at (5,3), flag FLAG_ITEM_MOSSDEEP_STEVENS_HOUSE_HM08
    """
    rows = [
        s
        for s in sources
        if s.item == "ITEM_HM08_DIVE" and s.map == "MossdeepCity_StevensHouse"
    ]
    assert len(rows) == 2
    gift, ball = sorted(rows, key=lambda s: int(s.source_line.split(":")[-1]))
    assert gift.macro == "giveitem" and gift.flag == "FLAG_RECEIVED_HM08"
    assert (gift.x, gift.y) == (None, None)
    assert "map_script table" in gift.unresolved
    assert ball.macro == "finditem" and (ball.x, ball.y) == (5, 3)
    assert ball.flag == "FLAG_ITEM_MOSSDEEP_STEVENS_HOUSE_HM08"


def test_ss_ticket_maps_out_of_a_shared_script(sources):
    """The give is in a globally-included file; the map is not in its path.

    `data/scripts/players_house.inc:379`  giveitem ITEM_SS_TICKET
    `data/scripts/players_house.inc:394`  setflag FLAG_RECEIVED_SS_TICKET
        -- fifteen lines downstream, which a fixed lookahead window misses
    `data/maps/LittlerootTown_BrendansHouse_1F/scripts.inc:57`  the OnFrame
        entry that names the script, and therefore the map
    """
    ticket = _one(sources, "ITEM_SS_TICKET")
    assert ticket.flag == "FLAG_RECEIVED_SS_TICKET"
    assert ticket.map == "LittlerootTown_BrendansHouse_1F"
    assert ticket.source_line == "data/scripts/players_house.inc:379"


def test_hidden_item_comes_straight_out_of_map_json(sources):
    """`data/maps/AbandonedShip_HiddenFloorRooms/map.json:136-141`

        {"type": "hidden_item", "x": 42, "y": 10, "elevation": 3,
         "item": "ITEM_ROOM_1_KEY",
         "flag": "FLAG_HIDDEN_ITEM_ABANDONED_SHIP_RM_1_KEY"}

    A hidden item has no script and no give line, so a script-only parser
    finds none of the 98 of them.
    """
    key = _one(sources, "ITEM_ROOM_1_KEY")
    assert (key.kind, key.map, key.x, key.y) == (
        "hidden",
        "AbandonedShip_HiddenFloorRooms",
        42,
        10,
    )
    assert key.flag == "FLAG_HIDDEN_ITEM_ABANDONED_SHIP_RM_1_KEY"
    assert key.script == "" and key.macro == ""
    assert key.source_line.startswith(
        "data/maps/AbandonedShip_HiddenFloorRooms/map.json:"
    )


def test_every_hm_has_at_least_one_source(sources, consts):
    """The FLY regression, generalised: no HM may be absent from the table."""
    found = {s.item for s in sources if s.item.startswith("ITEM_HM")}
    assert found == set(M.hm_items(consts)), "constants/items.h:339-346"


def test_source_histogram_is_stable(sources):
    """Counts, so a macro form quietly stopping to match is visible.

    Cross-check against the tree:
      136  `finditem ITEM_*`   (grep data/, excluding data/text and debug)
      128  `giveitem ITEM_*`   (140 total, 12 of them handed a VAR_*)
       98  `"type": "hidden_item"` bg_events
    """
    from collections import Counter

    assert dict(Counter(s.kind for s in sources)) == EXPECTED_KINDS
    assert dict(Counter(s.macro for s in sources)) == EXPECTED_MACROS
    assert len(sources) == sum(EXPECTED_KINDS.values())


def test_unresolved_rows_always_explain_themselves(sources):
    """The predecessor's worst defect class was an unexplained falsy value.

    Two invariants: a row missing coordinates says why, and a row that
    claims coordinates has both of them.
    """
    for src in sources:
        if src.x is None or not src.map or src.flag is None:
            assert src.unresolved, f"{src} is incomplete but gives no reason"
        assert (src.x is None) == (src.y is None), src
    assert all(s.unresolved for s in M.unresolved_sources())


def test_debug_only_gives_are_excluded(sources):
    """`data/scripts/debug.inc` is the developer menu (250 additem lines) and
    `LilycoveCity_ContestLobby_EventScript_SetDebug`
    (`data/maps/LilycoveCity_ContestLobby/scripts.inc:581-589`) is its
    in-map equivalent. Neither is reachable on a retail cartridge."""
    assert not [s for s in sources if "debug" in s.source_line.lower()]
    assert not [s for s in sources if "Debug" in s.script]


def test_runtime_gives_are_cited_not_dropped():
    """Gives whose item is computed cannot be named, so they are listed.

    `data/maps/LilycoveCity_DepartmentStore_1F/scripts.inc:113`
        giveitem VAR_LOTTERY_PRIZE
    `data/maps/Route123_BerryMastersHouse/scripts.inc:18`  giveitem VAR_RESULT
    """
    cites = M.runtime_gives()
    assert (
        "data/maps/LilycoveCity_DepartmentStore_1F/scripts.inc:113  "
        "giveitem VAR_LOTTERY_PRIZE" in cites
    )
    assert not [c for c in cites if c.startswith("data/scripts/obtain_item.inc")], (
        "Std_ObtainItem is the body giveitem expands into, not a give site"
    )


# ---- the ROM-resident halves -----------------------------------------


def test_hm_roster_comes_from_items_header(consts):
    """constants/items.h:339-346 -- ITEM_HM01_CUT .. ITEM_HM08_DIVE, and
    the HM block sits immediately after the 50 TMs."""
    hms = M.hm_items(consts)
    assert list(hms) == [
        "ITEM_HM01_CUT",
        "ITEM_HM02_FLY",
        "ITEM_HM03_SURF",
        "ITEM_HM04_STRENGTH",
        "ITEM_HM05_FLASH",
        "ITEM_HM06_ROCK_SMASH",
        "ITEM_HM07_WATERFALL",
        "ITEM_HM08_DIVE",
    ]
    first_tm = consts.items["ITEM_TM01_FOCUS_PUNCH"]
    assert (
        hms["ITEM_HM01_CUT"] - first_tm == consts.items["NUM_TECHNICAL_MACHINES"]
    ), "constants/items.h:369 -- the HM block follows the 50 TMs exactly"


def test_hm_moves_read_the_rom_table(emu, names, consts):
    """TMHMMoves, src/party_menu.c:117-177. Slot is itemId - ITEM_TM01
    (src/party_menu.c:3197), and the last eight slots are the HM moves."""
    moves = M.hm_moves(emu, names, consts)
    assert list(moves) == [
        "CUT",
        "FLY",
        "SURF",
        "STRENGTH",
        "FLASH",
        "ROCK SMASH",
        "WATERFALL",
        "DIVE",
    ]
    assert moves["FLY"] == consts.moves["MOVE_FLY"]
    assert moves["ROCK SMASH"] == consts.moves["MOVE_ROCK_SMASH"]
    assert emu.sym.size("TMHMMoves") == 2 * (50 + 8), "50 TMs + 8 HMs, u16 each"


def test_key_itemness_is_read_not_transcribed(names, consts):
    """gItems[].importance, src/item.c:19. Non-zero means untossable.

    src/data/items_en.h: HM01 importance 1 (line 5434), MACH BIKE 1
    (line 4154), POTION 0 (line 218).
    """
    assert M.is_key_item(consts.items["ITEM_HM01_CUT"], names)
    assert M.is_key_item(consts.items["ITEM_MACH_BIKE"], names)
    assert M.is_key_item(consts.items["ITEM_SS_TICKET"], names)
    assert not M.is_key_item(consts.items["ITEM_POTION"], names)
    assert not M.is_key_item(consts.items["ITEM_TM01_FOCUS_PUNCH"], names)


# ---- the live queries ------------------------------------------------


def test_missing_items_tracks_the_guarding_flag(emu, names, consts):
    """FLAG_RECEIVED_HM01 clear -> HM01 is listed; set -> it is gone."""
    clear = FakeState(emu, names, consts)
    assert "HM01" in {r["item"] for r in M.missing_items(clear, kind="hm")}

    got = FakeState(emu, names, consts, set_flags={"FLAG_RECEIVED_HM01"})
    rows = {r["item"] for r in M.missing_items(got, kind="hm")}
    assert "HM01" not in rows
    assert "HM02" in rows, "setting one flag must not hide the rest"


def test_missing_items_kinds_nest(emu, names, consts):
    """hm subset of key subset of all, and 'key' really means important."""
    state = FakeState(emu, names, consts)
    hm = {r["const"] for r in M.missing_items(state, kind="hm")}
    key = {r["const"] for r in M.missing_items(state, kind="key")}
    every = {r["const"] for r in M.missing_items(state, kind="all")}
    assert hm < key < every
    assert hm == set(M.hm_items(consts))
    assert "ITEM_POTION" in every and "ITEM_POTION" not in key
    with pytest.raises(ValueError, match="kind must be one of"):
        M.missing_items(state, kind="everything")


def test_missing_items_falls_back_to_the_bag(emu, names, consts):
    """A give with no flag at all -- the bike shop exchange,
    `data/maps/MauvilleCity_BikeShop/scripts.inc:47,53` -- can only be
    answered by looking in the bag."""
    empty = FakeState(emu, names, consts)
    assert "ACRO BIKE" in {r["item"] for r in M.missing_items(empty, kind="key")}

    owned = FakeState(emu, names, consts, bag={"ACRO BIKE": 1})
    assert "ACRO BIKE" not in {r["item"] for r in M.missing_items(owned, kind="key")}


def test_field_moves_names_the_holder(emu, names, consts):
    """"HM in the bag" is not "I can use it": the answer is per party mon."""
    consts_moves = consts.moves
    state = FakeState(
        emu,
        names,
        consts,
        party=[
            FakeMon("SWAMPY", (consts_moves["MOVE_SURF"], consts_moves["MOVE_TACKLE"])),
            FakeMon("SKARM", (consts_moves["MOVE_FLY"],)),
        ],
    )
    moves = M.field_moves(state)
    assert moves["SURF"] == "SWAMPY"
    assert moves["FLY"] == "SKARM"
    assert moves["DIVE"] is None
    assert set(moves) == set(M.hm_moves(emu, names, consts))


def test_field_moves_is_empty_with_an_empty_party(emu, names, consts):
    state = FakeState(emu, names, consts)
    assert set(M.field_moves(state).values()) == {None}


def test_status_fragment_is_short_and_placed(emu, names, consts):
    """The line appended to Driver.status(): HMs first, capped, with a count.

    HM01 is at (7,5) in RustboroCity_CuttersHouse
    (`data/maps/RustboroCity_CuttersHouse/map.json`).
    """
    state = FakeState(emu, names, consts)
    frag = M.status_fragment(state, kind="hm", limit=2)
    assert frag.startswith("missing: HM01(RustboroCity_CuttersHouse 7,5) HM02(")
    assert frag.endswith("more")
    # Ten rows, not eight: DIVE has three independent gates (Steven's gift
    # plus two item balls), and each is a place the player has to actually
    # go, so collapsing them would understate the work left.
    assert len(M.missing_items(state, kind="hm")) == 10
    assert "+8 more" in frag


# ---- the FLASH gate --------------------------------------------------


def test_dark_maps_come_from_requires_flash():
    """map.json's own boolean. GraniteCave_1F is lit and B1F is not, so
    keying on the tileset -- which the Crystal module had to do -- would
    invent a requirement the game does not have."""
    assert M.dark_maps() == frozenset(
        {
            "CaveOfOrigin_1F",
            "CaveOfOrigin_B1F",
            "CaveOfOrigin_B2F",
            "CaveOfOrigin_B3F",
            "GraniteCave_B1F",
            "GraniteCave_B2F",
            "VictoryRoad_B1F",
            "VictoryRoad_B2F",
        }
    )
    assert M.needs_flash("GraniteCave_B1F")
    assert not M.needs_flash("GraniteCave_1F")
    assert not M.needs_flash("Route102")


def test_needs_flash_refuses_an_unknown_map():
    """Answering False for a typo reads identically to "it is lit"."""
    with pytest.raises(KeyError, match="no map named"):
        M.needs_flash("GraniteCaveB1F")
