"""Every un-collected item is nameable, and the parser is pinned by VALUE.

A full Johto playthrough reached Champion with HM02 FLY still sitting with
Chuck's wife in Cianwood -- every journey of that run on foot -- because
nothing in the harness ever surfaced an un-collected key item. Two more of
the same class in one session: the S.S. Ticket (only Prof. Elm gives it)
and an uncollected MASTER BALL.

So these tests assert specific items at specific coordinates, each citing
the disassembly line it was cross-checked against, and cover all three
giver forms -- `verbosegiveitem`, plain `giveitem`, and `itemball` (HM07
WATERFALL is an itemball; a parser that only understood NPC gifts would
lose an HM).
"""
import pytest

import trek
from crystalagent import missables, paths
from trek import Driver

pytestmark = pytest.mark.unit

REPO = paths.REPO_ROOT


@pytest.fixture(scope="module")
def sources():
    return missables.parse_item_sources(REPO, trek._file_const)


@pytest.fixture(scope="module")
def pockets():
    return missables.parse_item_pockets(REPO)


def one(sources, item, map_const=None):
    rows = [s for s in sources
            if s.item == item and (map_const is None or s.map == map_const)]
    assert len(rows) == 1, rows
    return rows[0]


# -- the three giver forms, by value ------------------------------------

def test_hm02_fly_is_chucks_wife_in_cianwood(sources):
    """maps/CianwoodCity.asm:100 `verbosegiveitem HM_FLY`, guarded by
    EVENT_GOT_HM02_FLY (set at :102, checked at :86), inside
    CianwoodCityChucksWife -- the object_event at maps/CianwoodCity.asm:415
    which stands at (10,46). This is the row whose absence cost a whole
    playthrough its Fly."""
    s = one(sources, "HM_FLY")
    assert s.map == "CIANWOOD_CITY"
    assert (s.x, s.y) == (10, 46)
    assert s.kind == "gift"
    assert s.event == "EVENT_GOT_HM02_FLY"
    assert s.script == "CianwoodCityChucksWife"
    assert s.source_line == "maps/CianwoodCity.asm:100"


def test_hm07_waterfall_is_an_itemball_in_ice_path(sources):
    """maps/IcePath1F.asm:12 `itemball HM_WATERFALL`. Its coordinates and
    its event flag come from the OBJECT_EVENT (maps/IcePath1F.asm:31,
    OBJECTTYPE_ITEMBALL at 31,7 with EVENT_GOT_HM07_WATERFALL as its last
    field), not from a setevent in the script -- an itemball script is one
    line long."""
    s = one(sources, "HM_WATERFALL")
    assert s.map == "ICE_PATH_1F"
    assert (s.x, s.y) == (31, 7)
    assert s.kind == "itemball"
    assert s.event == "EVENT_GOT_HM07_WATERFALL"
    assert s.source_line == "maps/IcePath1F.asm:12"


def test_the_plain_giveitem_form_is_parsed_too(sources):
    """maps/BlackthornGym1F.asm:69 is `giveitem TM_DRAGONBREATH` (no
    'verbose'), guarded by the checkevent above it at :65 and owned by
    Clair's object_event at (5,3). The same TM is also given in
    DRAGONS_DEN_B1F, which is why the row is keyed by map."""
    s = one(sources, "TM_DRAGONBREATH", "BLACKTHORN_GYM_1F")
    assert (s.x, s.y) == (5, 3)
    assert s.event == "EVENT_GOT_TM24_DRAGONBREATH"
    assert s.source_line == "maps/BlackthornGym1F.asm:69"


def test_every_hm_appears_in_the_table(sources):
    """Regression against a parser that silently handles only
    `verbosegiveitem`: HM07 is an itemball, HM01/02/03/04/05/06 are gifts.
    Missing any one of them is the bug this module exists for."""
    found = {s.item for s in sources if s.item.startswith("HM_")}
    assert found == {"HM_CUT", "HM_FLY", "HM_SURF", "HM_STRENGTH",
                     "HM_FLASH", "HM_WHIRLPOOL", "HM_WATERFALL"}


def test_a_helper_script_gets_its_npcs_coordinates(sources):
    """The S.S. Ticket's give (maps/ElmsLab.asm:414) lives in
    `ElmGiveTicketScript`, which NO object_event names -- ProfElmScript
    reaches it with `iftrue` at :96. Resolving that hop is what turns
    "somewhere in ELMS_LAB" into "talk to the NPC at (5,2)". The MASTER
    BALL (:402) needs two hops (via ElmCheckMasterBall)."""
    ticket = one(sources, "S_S_TICKET")
    assert ticket.map == "ELMS_LAB" and (ticket.x, ticket.y) == (5, 2)
    assert ticket.event == "EVENT_GOT_SS_TICKET_FROM_ELM"
    assert ticket.source_line == "maps/ElmsLab.asm:414"
    ball = one(sources, "MASTER_BALL", "ELMS_LAB")
    assert (ball.x, ball.y) == (5, 2)
    assert ball.event == "EVENT_GOT_MASTER_BALL_FROM_ELM"


def test_unresolvable_coordinates_are_reported_not_guessed(sources):
    """A give inside a cutscene or a map script has no owning
    object_event: MYSTERY_EGG (maps/MrPokemonsHouse.asm:31) is handed over
    by a scripted scene. Those rows carry x=y=None rather than a guess."""
    egg = one(sources, "MYSTERY_EGG")
    assert (egg.x, egg.y) == (None, None)
    assert egg.event == "EVENT_GOT_MYSTERY_EGG_FROM_MR_POKEMON"
    unresolved = [s for s in sources if s.x is None]
    # a handful, not a systemic failure (322 sources at time of writing)
    assert 0 < len(unresolved) < len(sources) // 8


def test_the_giver_count_matches_the_disassembly(sources):
    """`grep -c` over maps/*.asm: 115 verbosegiveitem + 29 giveitem = 144
    gifts, and 178 itemballs."""
    assert sum(1 for s in sources if s.kind == "gift") == 144
    assert sum(1 for s in sources if s.kind == "itemball") == 178


# -- which items are "key" comes from the game's own pockets ------------

def test_item_pockets_are_the_games_pockets(pockets):
    """data/items/attributes.asm: the 5th field of `item_attribute` is the
    pocket. S.S. TICKET / BICYCLE / SQUIRTBOTTLE are KEY_ITEM, a POTION is
    ITEM, a POKE BALL is BALL."""
    assert pockets["S_S_TICKET"] == "KEY_ITEM"
    assert pockets["BICYCLE"] == "KEY_ITEM"
    assert pockets["SQUIRTBOTTLE"] == "KEY_ITEM"
    assert pockets["POTION"] == "ITEM"
    assert pockets["POKE_BALL"] == "BALL"


def test_key_items_are_key_items_plus_the_hms(pockets):
    """HMs live in the TM_HM pocket but are the entire point, so
    `is_key_item` adds them explicitly."""
    assert missables.is_key_item("HM_FLY", pockets)
    assert missables.is_key_item("S_S_TICKET", pockets)
    assert not missables.is_key_item("POTION", pockets)
    assert not missables.is_key_item("MASTER_BALL", pockets)


def test_hm_item_constants_map_to_their_bag_tags():
    """The bag stores HMs by NUMBER (wTMsHMs), the scripts name them
    HM_FLY: without this mapping "do I have it?" cannot be answered.
    HM numbering is the add_hm order in constants/item_constants.asm."""
    tags = missables.hm_item_tags(REPO)
    assert tags["HM_CUT"] == "HM01"
    assert tags["HM_FLY"] == "HM02"
    assert tags["HM_WATERFALL"] == "HM07"
    assert missables.hm_moves(REPO)["HM02"] == "FLY"


# -- the live query ----------------------------------------------------

def rows_for(flags, bag=(), kind="key"):
    srcs = missables.parse_item_sources(REPO, trek._file_const)
    return missables.missing_items(
        srcs, have_event=lambda f: flags.get(f, False), bag=dict(bag),
        repo=REPO, kind=kind)


def test_fly_is_listed_until_its_event_flag_is_set():
    clear = rows_for({})
    assert any(r["item"] == "HM_FLY" for r in clear)
    fly = next(r for r in clear if r["item"] == "HM_FLY")
    assert fly["have"] is False
    assert (fly["map"], fly["x"], fly["y"]) == ("CIANWOOD_CITY", 10, 46)
    assert fly["source"] == "maps/CianwoodCity.asm:100"
    got = rows_for({"EVENT_GOT_HM02_FLY": True})
    assert not any(r["item"] == "HM_FLY" for r in got)


def test_hms_are_listed_first():
    """An HM is the difference between walking and flying; a Coin Case is
    not. The ordering puts them where a capped status line will show
    them."""
    rows = rows_for({})
    firsts = [r["item"] for r in rows][:7]
    assert all(i.startswith("HM_") for i in firsts), firsts


def test_an_item_with_no_event_flag_falls_back_to_the_bag():
    """BLUE_CARD (maps/RadioTower2F.asm:167) has no EVENT_GOT_* guard, so
    the bag is the only evidence."""
    assert any(r["item"] == "BLUE_CARD" for r in rows_for({}))
    with_card = rows_for({}, bag={"BLUECARD": 1})
    assert not any(r["item"] == "BLUE_CARD" for r in with_card)


def test_an_hm_in_the_bag_counts_as_obtained_without_its_flag():
    """The bag names HMs by tag, so 'HM02' must satisfy 'HM_FLY'."""
    rows = rows_for({}, bag={"HM02": 1})
    assert not any(r["item"] == "HM_FLY" for r in rows)


def test_kind_all_is_the_whole_table_and_key_is_a_subset():
    key, all_rows = rows_for({}), rows_for({}, kind="all")
    assert len(all_rows) > len(key) * 5
    assert {r["item"] for r in key} <= {r["item"] for r in all_rows}
    assert any(r["item"] == "POTION" for r in all_rows)
    assert not any(r["item"] == "POTION" for r in key)


def test_the_status_fragment_is_short_and_names_the_place():
    """With only FLY outstanding among the HMs, the capped fragment leads
    with exactly the line that would have saved the pt12 detour."""
    got_all_but_fly = {f"EVENT_GOT_HM0{n}_{name}": True for n, name in
                       ((1, "CUT"), (3, "SURF"), (4, "STRENGTH"),
                        (5, "FLASH"), (6, "WHIRLPOOL"), (7, "WATERFALL"))}
    rows = rows_for(got_all_but_fly)
    frag = missables.status_fragment(rows, limit=2)
    assert frag.startswith("missing: FLY(CIANWOOD_CITY 10,46)")
    assert frag.endswith(f"+{len(rows) - 2} more")
    assert missables.status_fragment([]) == ""


# -- field_moves: "HM in the bag" is not "I can use it" ----------------

class FakeEmu:
    def __init__(self):
        self.frame = 0
        self.rows = [" " * 20 for _ in range(18)]

    def read_u8(self, sym):
        return 0


def driver_with_party(party, monkeypatch):
    d = Driver.__new__(Driver)
    d.emu = FakeEmu()
    d.names = trek.Driver.names if False else _Names()
    monkeypatch.setattr(trek, "game_state",
                        lambda emu, names, **kw: {"party": party})
    return d


class _Names:
    moves = {1: "CUT", 2: "FLY", 3: "SURF", 4: "STRENGTH", 5: "FLASH",
             6: "WHIRLPOOL", 7: "WATERFALL"}
    items = {}
    species = {}


def test_field_moves_reports_the_knower_or_none(monkeypatch):
    party = [
        {"nickname": "GATOR", "name": "FERALIGATR",
         "moves": [{"name": "SURF"}, {"name": "STRENGTH"}]},
        {"nickname": "REED", "name": "NOCTOWL",
         "moves": [{"name": "CUT"}]},
    ]
    d = driver_with_party(party, monkeypatch)
    moves = d.field_moves()
    assert moves["SURF"] == "GATOR"
    assert moves["STRENGTH"] == "GATOR"
    assert moves["CUT"] == "REED"
    # the one fact that would have saved the whole detour
    assert moves["FLY"] is None
    assert moves["WATERFALL"] is None
    assert set(moves) == {"CUT", "FLY", "SURF", "STRENGTH", "FLASH",
                          "WHIRLPOOL", "WATERFALL"}


def test_field_moves_prefers_the_nickname(monkeypatch):
    party = [{"nickname": "BUBBLES", "name": "LAPRAS",
              "moves": [{"name": "SURF"}]}]
    d = driver_with_party(party, monkeypatch)
    assert d.field_moves()["SURF"] == "BUBBLES"
