"""teach_tm: a TM goes to a NAMED mon, or nothing happens at all.

TMs are one-shot consumables, so a flow that opens menus and then
discovers the mon is not compatible has already cost the run something no
savestate-free session gets back. Every checkable refusal therefore
happens BEFORE the first button press, off the game's own data: the
TM/HM number->move table in constants/item_constants.asm, the species'
`tmhm` learnset in data/pokemon/base_stats, and the live wTMsHMs counts.
"""
import pytest

import trek
from trek import Driver
from crystalagent.tactics import parse_species_tmhm, parse_tmhm_moves
from crystalagent import paths

pytestmark = pytest.mark.unit

TMHM = parse_tmhm_moves(paths.REPO_ROOT)
LEARNSETS = parse_species_tmhm(paths.REPO_ROOT)
IRON_TAIL_TAG = next(t for t, mv in TMHM.items() if mv == "IRON_TAIL")


class FakeEmu:
    def __init__(self, stock=()):
        self.frame = 0
        self.rows = [" " * 20 for _ in range(18)]
        self.u8 = {}
        self.pressed = []
        self.sym = {"wTMsHMs": (1, 0xD859)}
        # one byte per TMNUM, TM01..TM50 then HM01..HM07
        self.tms = bytearray(len(TMHM))
        for tag, n in dict(stock).items():
            self.tms[list(TMHM).index(tag)] = n

    def tick(self, n=1):
        self.frame += n

    def screen_text(self):
        return list(self.rows)

    def read_u8(self, sym):
        return self.u8.get(sym, 0)

    def read(self, loc, n):
        assert loc == self.sym["wTMsHMs"], loc
        return bytes(self.tms[:n])

    def run_sequence(self, seq):
        self.pressed.append(str(seq))
        self.frame += 24


def driver(party, stock=(("TM23", 1),)):
    d = Driver.__new__(Driver)
    d.emu = FakeEmu(stock)
    d.names = SimpleNames()
    d.press = lambda seq: d.emu.pressed.append(seq)
    d.close_menus = lambda *a, **k: True
    d.textbox = lambda: False
    d._party = party
    return d


class SimpleNames:
    """Just the move-name table _resolve_tm walks."""
    moves = {1: "IRON TAIL", 2: "DRAGONBREATH", 3: "SURF", 4: "BITE",
             5: "SCARY FACE", 6: "ZAP CANNON", 7: "HYDRO PUMP"}
    items = {}
    species = {}


def mon(name="FERALIGATR", nickname="GATOR", moves=("SURF", "BITE")):
    return {"name": name, "species": 160, "nickname": nickname,
            "moves": [{"name": m, "pp": 10} for m in moves],
            "level": 52, "hp": 100, "max_hp": 100, "egg": False}


@pytest.fixture
def patched(monkeypatch):
    """game_state reads the driver's fake party."""
    def fake_state(emu, names, **kw):
        return {"party": list(emu.party)}
    monkeypatch.setattr(trek, "game_state", fake_state)
    return fake_state


def run(d, *args, **kw):
    d.emu.party = d._party
    return d.teach_tm(*args, **kw)


# -- the data itself -----------------------------------------------------

def test_the_tm_table_and_learnset_are_the_games_own():
    """constants/item_constants.asm numbers TMs by add_tm ORDER (item ids
    interleave non-TM entries), and feraligatr.asm:20 lists its learnset.
    Crystal's TM24 is DRAGONBREATH, not RBY's THUNDERBOLT -- a hardcoded
    table would have been wrong here."""
    assert TMHM["TM01"] == "DYNAMICPUNCH"
    assert TMHM["TM24"] == "DRAGONBREATH"
    assert TMHM["HM03"] == "SURF"
    assert len(TMHM) == 57
    assert "IRON_TAIL" in LEARNSETS["FERALIGATR"]
    assert "ZAP_CANNON" not in LEARNSETS["FERALIGATR"]


# -- refusals happen before any button press ----------------------------

def test_an_incompatible_mon_is_refused_without_touching_the_ui(patched):
    """FERALIGATR cannot learn ZAP CANNON. The old way to find that out
    was to open the pack, use the TM, and read the game's refusal."""
    zap = next(t for t, mv in TMHM.items() if mv == "ZAP_CANNON")
    d = driver([mon()], stock=((zap, 1),))
    assert run(d, zap, "GATOR") is False
    assert d.last_tm_reason.startswith("cannot-learn")
    assert d.emu.pressed == []


def test_a_tm_that_is_not_held_is_refused(patched):
    d = driver([mon()], stock=())
    assert run(d, "IRON TAIL", "GATOR") is False
    assert d.last_tm_reason.startswith("not-in-bag")
    assert d.emu.pressed == []


def test_an_unknown_tm_name_is_refused(patched):
    d = driver([mon()])
    assert run(d, "TM99", "GATOR") is False
    assert d.last_tm_reason.startswith("unknown-tm")
    assert run(d, "HYPERSPACE FURY", "GATOR") is False
    assert d.last_tm_reason.startswith("unknown-tm")


def test_a_mon_that_already_knows_the_move_is_refused(patched):
    d = driver([mon(moves=("IRON TAIL", "SURF"))])
    assert run(d, IRON_TAIL_TAG, "GATOR") is False
    assert d.last_tm_reason.startswith("already-knows")
    assert d.emu.pressed == []


def test_an_unknown_mon_raises_rather_than_teaching_the_wrong_one(patched):
    d = driver([mon()])
    with pytest.raises(ValueError, match="no party member named"):
        run(d, IRON_TAIL_TAG, "NOBODY")


def test_forgetting_an_hm_move_raises(patched):
    """The game refuses to delete HM moves; confirming loops the refusal."""
    d = driver([mon(moves=("SURF", "BITE", "SCARY FACE", "DRAGONBREATH"))])
    with pytest.raises(ValueError, match="HM move"):
        run(d, IRON_TAIL_TAG, "GATOR", forget="SURF")


def test_forgetting_a_move_the_mon_does_not_know_raises(patched):
    d = driver([mon()])
    with pytest.raises(ValueError, match="does not know"):
        run(d, IRON_TAIL_TAG, "GATOR", forget="HYDRO PUMP")


# -- the accepted path --------------------------------------------------

def test_a_compatible_mon_is_taught_and_verified(patched):
    d = driver([mon(nickname="BROOK"), mon(nickname="GATOR")])
    steps = []
    d._tmhm_pocket = lambda: steps.append("pocket") or True
    d._tmhm_row = lambda tag, move: steps.append((tag, move)) or True
    d._tmhm_use = lambda: steps.append("use") or True
    d._party_cursor_to = lambda row: steps.append(("row", row)) or True
    d._able_under_cursor = lambda: True
    d._walk_forget_menu = lambda move, forget=None, slot=None: \
        steps.append(("learn", move, forget)) or True
    assert run(d, "IRON TAIL", "GATOR", forget="BITE") is True
    assert d.last_tm_reason == "learned"
    assert steps == ["pocket", (IRON_TAIL_TAG, "IRON TAIL"), "use",
                     ("row", 2), ("learn", "IRON TAIL", "BITE")]


def test_the_species_name_also_names_the_mon(patched):
    d = driver([mon(nickname="GATOR")])
    d._tmhm_pocket = lambda: True
    d._tmhm_row = lambda tag, move: True
    d._tmhm_use = lambda: True
    rows = []
    d._party_cursor_to = lambda row: rows.append(row) or True
    d._able_under_cursor = lambda: True
    d._walk_forget_menu = lambda move, forget=None, slot=None: True
    assert run(d, IRON_TAIL_TAG, "feraligatr") is True
    assert rows == [1]


def test_a_flow_that_does_not_leave_the_move_behind_is_a_failure(patched):
    """Menus can flow perfectly while nothing was learned; only the mon's
    move list proves it."""
    d = driver([mon()])
    d._tmhm_pocket = lambda: True
    d._tmhm_row = lambda tag, move: True
    d._tmhm_use = lambda: True
    d._party_cursor_to = lambda row: True
    d._able_under_cursor = lambda: True
    d._walk_forget_menu = lambda move, forget=None, slot=None: False
    assert run(d, IRON_TAIL_TAG, "GATOR") is False
    assert d.last_tm_reason.startswith("not-learned")


def test_the_games_own_not_able_verdict_stops_the_flow(patched):
    """The learnset says yes but the party list says NOT ABLE: believe the
    screen and back out instead of pressing A on it."""
    d = driver([mon()])
    d._tmhm_pocket = lambda: True
    d._tmhm_row = lambda tag, move: True
    d._tmhm_use = lambda: True
    d._party_cursor_to = lambda row: True
    d._able_under_cursor = lambda: False
    closed = []
    d.close_menus = lambda *a, **k: closed.append(1) or True
    assert run(d, IRON_TAIL_TAG, "GATOR") is False
    assert d.last_tm_reason.startswith("not-able")
    assert closed == [1]


# -- the shared forget walk ---------------------------------------------

def test_the_forget_walk_targets_the_named_move():
    """teach_hm and teach_tm share this walk. Without a name it confirms
    whatever the list opens on (slot 1) -- which is how GATOR's BITE
    became SCARY FACE."""
    d = Driver.__new__(Driver)
    d.emu = FakeEmu()
    order = ["SURF", "BITE", "SCARY FACE", "DRAGONBREATH"]
    state = {"cursor": 0, "learned": False, "picked": None}
    d.textbox = lambda: False
    d.cursor_rows = lambda: [f"▶{order[state['cursor']]}"]
    d.observe = lambda: {"party": [{
        "nickname": "GATOR",
        "moves": [{"name": n} for n in order]}]}

    def press(seq):
        d.emu.pressed.append(seq)
        if seq.startswith("D"):
            state["cursor"] = (state["cursor"] + 1) % len(order)
        elif seq.startswith("A"):
            # A on the move list confirms the highlighted row
            state["picked"] = order[state["cursor"]]
            order[state["cursor"]] = "IRON TAIL"
            state["learned"] = True

    d.press = press
    d.emu.rows = [" Which move should", " be forgotten?"] + \
        [" " * 20 for _ in range(16)]
    assert d._walk_forget_menu("IRON TAIL", "SCARY FACE", slot=0) is True
    assert state["picked"] == "SCARY FACE"
