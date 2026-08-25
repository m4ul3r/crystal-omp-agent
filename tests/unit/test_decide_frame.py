"""claude-wren pt6: the model-facing decision surface (crystalagent.decide).

Three things the live run proved missing:

(a) battle_frame() -- one call that assembles what every hand-written policy
    was re-deriving from game_state()/observe() (and usually got wrong):
    my mon, the enemy, the party, the bag, the switchable slots, and my
    moves WITH the type multiplier against the mon actually standing there.
(b) can_switch -- the ('switch', i) wedges were switches into fainted mons
    and into the mon already out.
(c) TurnLog / free_hits() -- the Koga wipe (five of six mons lost to a
    ping-pong switch policy) left no per-turn record at all, so nobody could
    see the ~10 turns the enemy got for free.

All duck-typed fakes: real WRAM layout arithmetic, no emulator.
"""
import re
from types import SimpleNamespace

import pytest

from crystalagent.battle import Battle, BattleData
from crystalagent.decide import (DecisionRequired, TurnLog, battle_frame,
                                 explain, read_bag, read_party)

pytestmark = pytest.mark.unit

MON_NAME_LENGTH = 11
PARTY_STRIDE = 0x30


class FakeSym(dict):
    def offset(self, a, b):
        return self[a][1] - self[b][1]


def _sym_table():
    """Self-consistent addresses; only the labels decide.py/battle.py touch.
    Party struct field offsets mirror the real PartyMon layout order
    (species, moves, .., level, status, .., hp, maxhp)."""
    party_fields = {"": 0x00, "Species": 0x00, "Moves": 0x02, "Level": 0x1F,
                    "Status": 0x20, "HP": 0x22, "MaxHP": 0x24, "PP": 0x28}
    sym = {
        "wBattleMon":          (0, 0x100),
        "wBattleMonSpecies":   (0, 0x100),
        "wBattleMonMoves":     (0, 0x102),
        "wBattleMonPP":        (0, 0x106),
        "wBattleMonLevel":     (0, 0x10A),
        "wBattleMonStatus":    (0, 0x10B),
        "wBattleMonHP":        (0, 0x10C),
        "wBattleMonMaxHP":     (0, 0x10E),
        "wBattleMonType":      (0, 0x110),
        "wEnemyMon":           (0, 0x200),
        "wEnemyMonSpecies":    (0, 0x200),
        "wEnemyMonLevel":      (0, 0x20A),
        "wEnemyMonStatus":     (0, 0x20B),
        "wEnemyMonHP":         (0, 0x20C),
        "wEnemyMonMaxHP":      (0, 0x20E),
        "wEnemyMonType":       (0, 0x210),
        "wCurBattleMon":       (0, 0x300),
        "wCurOTMon":           (0, 0x301),
        "wBattleMode":         (0, 0x302),
        "wPartyCount":         (0, 0x303),
        "wPlayerTurnsTaken":   (0, 0x304),
        "wPartySpecies":       (0, 0x308),
        "wEnemyMonNickname":   (0, 0x310),
        "wNumItems":           (0, 0x320),
        "wItems":              (0, 0x330),
        "wNumBalls":           (0, 0x360),
        "wBalls":              (0, 0x370),
        "wPartyMonNicknames":  (1, 0x400),
        "wPartyMon1":          (1, 0x500),
        "wPartyMon2":          (1, 0x500 + PARTY_STRIDE),
    }
    for field, off in party_fields.items():
        sym["wPartyMon1" + field] = (1, 0x500 + off)
    return FakeSym(sym)


class FakeCharmap:
    """Gen-2-style charmap stand-in: letters at ord(c) + 0x80, $50
    terminates. (Plain ASCII is a trap -- 'P' IS 0x50.)"""

    def decode(self, data, stop_at_terminator=True):
        out = []
        for b in data:
            if stop_at_terminator and b == 0x50:
                break
            out.append(chr(b - 0x80) if b >= 0x80 else "?")
        return "".join(out)


class FakeEmu:
    def __init__(self, sym):
        self.sym = sym
        self.charmap = FakeCharmap()
        self.mem = {}
        self.frame = 0

    def _resolve(self, x):
        return x if isinstance(x, tuple) else self.sym[x]

    def poke(self, name_or_addr, data):
        bank, addr = self._resolve(name_or_addr)
        if isinstance(data, int):
            data = bytes([data])
        for i, b in enumerate(data):
            self.mem[(bank, addr + i)] = b

    def read(self, name_or_addr, n=1):
        bank, addr = self._resolve(name_or_addr)
        return bytes(self.mem.get((bank, addr + i), 0) for i in range(n))

    def read_u8(self, name):
        return self.read(name, 1)[0]

    def read_be(self, name, n):
        return int.from_bytes(self.read(name, n), "big")

    def read_text(self, name, n):
        return self.charmap.decode(self.read(name, n))

    def screen_text(self):
        return []


def _nick(s):
    return bytes(ord(c) + 0x80 for c in s) + b"\x50"


# -- a real BattleData without the ROM: hand-built chart + move table --------

TYPES = {"NORMAL": 0, "WATER": 21, "ROCK": 5, "GROUND": 4, "ICE": 25}

CHART = {
    ("WATER", "ROCK"): 2.0,
    ("WATER", "GROUND"): 2.0,
    ("WATER", "WATER"): 0.5,
    ("ICE", "WATER"): 0.5,
    ("ICE", "GROUND"): 2.0,
    ("NORMAL", "ROCK"): 0.5,
}

MOVES = {
    # id: name, effect-relevant fields only
    55: ("WATER GUN", {"effect": 0, "power": 40, "type": TYPES["WATER"],
                       "accuracy": 100}),
    57: ("SURF", {"effect": 0, "power": 95, "type": TYPES["WATER"],
                  "accuracy": 100}),
    58: ("ICE BEAM", {"effect": 0, "power": 95, "type": TYPES["ICE"],
                      "accuracy": 100}),
    33: ("TACKLE", {"effect": 0, "power": 35, "type": TYPES["NORMAL"],
                    "accuracy": 95}),
}

SPECIES = {9: "BLASTOISE", 95: "ONIX", 130: "GYARADOS", 160: "FERALIGATR",
           176: "TOGETIC", 241: "MILTANK"}

# the pack's real name for a Poké Ball is "# BALL" -- charmap $54's token
# is "#" (constants/charmap.asm: 'POKé'), which is what names.items holds
ITEMS = {17: "SUPER POTION", 4: "# BALL", 5: "GREAT BALL"}


def make_bdata():
    """A real BattleData object (its own effectiveness()), populated
    without touching the ROM or the disassembly."""
    bd = BattleData.__new__(BattleData)
    bd.types = dict(TYPES)
    bd.matchups = {(TYPES[a], TYPES[d]): m for (a, d), m in CHART.items()}
    bd.moves = {mid: dict(rec) for mid, (_, rec) in MOVES.items()}
    return bd


def make_names():
    return SimpleNamespace(
        species=dict(SPECIES),
        moves={mid: name for mid, (name, _) in MOVES.items()},
        items=dict(ITEMS))


def make_emu(enemy_types=(TYPES["ROCK"], TYPES["GROUND"]), mode=1,
             enemy_species=95, my_hp=44, enemy_hp=60):
    """Battle in progress: party slot 1 (BROOK, FERALIGATR) is out with
    WATER GUN / SURF / ICE BEAM / TACKLE. Party: GATOR (fainted), BROOK
    (active), PEBBLE (an EGG), MOO (alive)."""
    emu = FakeEmu(_sym_table())
    emu.poke("wBattleMode", mode)
    emu.poke("wPlayerTurnsTaken", 3)

    emu.poke("wCurBattleMon", 1)
    emu.poke("wBattleMonSpecies", 160)
    emu.poke("wBattleMonMoves", bytes([55, 57, 58, 33]))
    emu.poke("wBattleMonPP", bytes([25, 15, 0, 35]))
    emu.poke("wBattleMonLevel", 30)
    emu.poke("wBattleMonStatus", 0x08)                 # PSN
    emu.poke("wBattleMonHP", my_hp.to_bytes(2, "big"))
    emu.poke("wBattleMonMaxHP", (80).to_bytes(2, "big"))
    emu.poke("wBattleMonType", bytes([TYPES["WATER"], TYPES["WATER"]]))

    emu.poke("wCurOTMon", 2)
    emu.poke("wEnemyMonNickname", _nick(SPECIES[enemy_species]))
    emu.poke("wEnemyMonSpecies", enemy_species)
    emu.poke("wEnemyMonLevel", 28)
    emu.poke("wEnemyMonStatus", 0)
    emu.poke("wEnemyMonHP", enemy_hp.to_bytes(2, "big"))
    emu.poke("wEnemyMonMaxHP", (60).to_bytes(2, "big"))
    emu.poke("wEnemyMonType", bytes(enemy_types))

    # party: 4 slots, slot 0 fainted, slot 2 an EGG
    emu.poke("wPartyCount", 4)
    emu.poke("wPartySpecies", bytes([160, 160, 0xFD, 241]))
    nb, na = emu.sym["wPartyMonNicknames"]
    for i, name in enumerate(("GATOR", "BROOK", "PEBBLE", "MOO")):
        emu.poke((nb, na + i * MON_NAME_LENGTH), _nick(name))
    pb, pa = emu.sym["wPartyMon1"]
    roster = [(160, 30, 0, 78, 0x00), (160, 30, my_hp, 80, 0x08),
              (176, 5, 20, 20, 0x00), (241, 27, 65, 70, 0x40)]
    for i, (species, level, hp, max_hp, status) in enumerate(roster):
        base = pa + i * PARTY_STRIDE
        emu.poke((pb, base + 0x00), species)
        emu.poke((pb, base + 0x1F), level)
        emu.poke((pb, base + 0x20), status)
        emu.poke((pb, base + 0x22), hp.to_bytes(2, "big"))
        emu.poke((pb, base + 0x24), max_hp.to_bytes(2, "big"))

    # bag: 2 pockets, (id, qty) pairs
    emu.poke("wNumItems", 1)
    emu.poke("wItems", bytes([17, 3]))
    emu.poke("wNumBalls", 2)
    emu.poke("wBalls", bytes([4, 7, 5, 2]))
    return emu


def make_frame(**kw):
    emu = make_emu(**kw)
    return battle_frame(emu, make_names(), make_bdata())


# -- (a) the frame carries the whole contract --------------------------------

CONTRACT_KEYS = {"me", "enemy", "party", "bag", "turn", "wild", "can_switch",
                 "moves"}


def test_frame_has_exactly_the_contract_keys():
    assert set(make_frame()) == CONTRACT_KEYS


def test_frame_me_and_enemy_carry_identity_vitals_and_status():
    f = make_frame()
    me, enemy = f["me"], f["enemy"]
    for side in (me, enemy):
        assert {"nickname", "species", "level", "hp", "max_hp", "types",
                "status"} <= set(side)
    assert (me["nickname"], me["species"], me["level"]) == \
        ("BROOK", "FERALIGATR", 30)
    assert (me["hp"], me["max_hp"]) == (44, 80)
    assert me["status"] == ["PSN"]
    assert me["types"] == ["WATER"]          # stored twice, listed once
    assert (enemy["species"], enemy["level"]) == ("ONIX", 28)
    assert (enemy["hp"], enemy["max_hp"]) == (60, 60)
    assert enemy["types"] == ["ROCK", "GROUND"]
    assert enemy["status"] == []


def test_frame_turn_and_wild_flag():
    f = make_frame()
    assert f["turn"] == 3                    # wPlayerTurnsTaken
    assert f["wild"] is True
    assert make_frame(mode=2)["wild"] is False


def test_turn_can_be_overridden_by_the_caller():
    emu = make_emu()
    assert battle_frame(emu, make_names(), make_bdata(), turn=11)["turn"] == 11


def test_frame_bag_lists_both_battle_pockets_with_loose_lookup():
    bag = make_frame()["bag"]
    assert bag["SUPER POTION"] == 3
    assert bag["GREAT BALL"] == 2
    # the pack's own name uses the game's POKé glyph; policies say POKE BALL
    assert bag["POKE BALL"] == 7
    assert bag.get("Super Potion") == 3
    assert bag.get("FULL RESTORE") is None
    assert bag.quantity("FULL RESTORE") == 0
    assert "GREATBALL" in bag


def test_frame_accepts_a_live_battle_or_the_long_form():
    emu = make_emu()
    names, bdata = make_names(), make_bdata()
    b = Battle(emu, names, bdata)
    assert battle_frame(b) == battle_frame(emu, names, bdata)


# -- (b) moves carry the multiplier against THIS enemy -----------------------

def _mult(frame, move_name):
    return next(m["effect_mult"] for m in frame["moves"]
                if m["name"] == move_name)


def test_move_entries_have_exactly_the_contract_fields():
    for m in make_frame()["moves"]:
        assert set(m) == {"slot", "name", "type", "power", "pp",
                          "effect_mult"}
    slots = [(m["slot"], m["name"], m["power"], m["pp"], m["type"])
             for m in make_frame()["moves"]]
    assert slots == [(0, "WATER GUN", 40, 25, "WATER"),
                     (1, "SURF", 95, 15, "WATER"),
                     (2, "ICE BEAM", 95, 0, "ICE"),
                     (3, "TACKLE", 35, 35, "NORMAL")]


def test_effect_mult_is_4x_for_water_against_rock_ground():
    f = make_frame()                        # ONIX: ROCK/GROUND
    assert _mult(f, "SURF") == 4.0
    assert _mult(f, "WATER GUN") == 4.0
    assert _mult(f, "ICE BEAM") == 2.0      # ICE vs GROUND only
    assert _mult(f, "TACKLE") == 0.5


def test_effect_mult_is_half_for_water_against_a_mono_water_enemy():
    """GYARADOS stores WATER twice (Gen 2 mono types are duplicated). The
    engine's CheckTypeMatchup applies a matchup row ONCE, so this is 0.5x
    -- not the 0.25x a naive per-entry product gives."""
    f = make_frame(enemy_types=(TYPES["WATER"], TYPES["WATER"]),
                   enemy_species=130)
    assert f["enemy"]["types"] == ["WATER"]
    assert _mult(f, "SURF") == 0.5
    assert _mult(f, "ICE BEAM") == 0.5


# -- (c) can_switch excludes the active, the fainted and the eggs ------------

def test_can_switch_excludes_active_fainted_and_eggs():
    f = make_frame()
    # slot 0 fainted, slot 1 active, slot 2 EGG, slot 3 alive
    assert f["can_switch"] == [3]
    assert [(p["index"], p["nickname"], p["fainted"], p["egg"])
            for p in f["party"]] == [
        (0, "GATOR", True, False), (1, "BROOK", False, False),
        (2, "PEBBLE", False, True), (3, "MOO", False, False)]
    assert f["party"][3]["status"] == ["PAR"]
    assert f["party"][3]["species"] == "MILTANK"


def test_can_switch_offers_a_healthy_slot_once_the_lead_is_healed():
    emu = make_emu()
    pb, pa = emu.sym["wPartyMon1"]
    emu.poke((pb, pa + 0x22), (40).to_bytes(2, "big"))   # slot 0 revived
    f = battle_frame(emu, make_names(), make_bdata())
    assert f["can_switch"] == [0, 3]


def test_caller_supplied_party_is_used_verbatim():
    """A caller that already read state.game_state()['party'] can hand it
    over instead of paying for a second WRAM walk -- game_state keys the id
    as 'species' and the name as 'name'."""
    party = [
        {"species": 160, "name": "FERALIGATR", "nickname": "GATOR",
         "level": 30, "hp": 0, "max_hp": 78, "status": [], "egg": False},
        {"species": 160, "name": "FERALIGATR", "nickname": "BROOK",
         "level": 30, "hp": 44, "max_hp": 80, "status": ["PSN"],
         "egg": False},
        {"species": 241, "name": "MILTANK", "nickname": "MOO", "level": 27,
         "hp": 65, "max_hp": 70, "status": ["PAR"], "egg": False},
    ]
    f = battle_frame(make_emu(), make_names(), make_bdata(), party=party)
    assert [p["species"] for p in f["party"]] == \
        ["FERALIGATR", "FERALIGATR", "MILTANK"]
    assert f["party"][0]["fainted"] is True
    assert f["can_switch"] == [2]


def test_read_party_and_read_bag_are_usable_on_their_own():
    emu, names = make_emu(), make_names()
    assert [p["nickname"] for p in read_party(emu, names)] == \
        ["GATOR", "BROOK", "PEBBLE", "MOO"]
    assert read_bag(emu, names, pockets=("items",)) == {"SUPER POTION": 3}


# -- explain() ---------------------------------------------------------------

def test_explain_is_one_line_naming_both_sides_and_the_multipliers():
    line = explain(make_frame())
    assert "\n" not in line
    assert "BROOK" in line and "ONIX" in line
    assert "SURF" in line and "x4" in line
    assert "wild" in line
    assert "SUPER POTION x3" in line


# -- TurnLog -----------------------------------------------------------------

def _koga_log():
    """The Koga shape: two ping-pong switch-ins, an item turn, then one real
    attack. Before this record existed the run only knew it had lost."""
    t = TurnLog()
    t.record(actor="me", action=("switch", 3), enemy_species=95,
             enemy_hp_before=60, enemy_hp_after=60,
             my_hp_before=65, my_hp_after=41, note="ping-pong switch")
    t.record(actor="me", action=("item", "SUPER POTION"), enemy_species=95,
             enemy_hp_before=60, enemy_hp_after=60,
             my_hp_before=41, my_hp_after=52,
             note="healed 50, then took 39")
    t.record(actor="me", action=("attack", 1), enemy_species=95,
             enemy_hp_before=60, enemy_hp_after=12,
             my_hp_before=52, my_hp_after=40)
    return t


def test_rows_are_append_only_with_the_contract_fields():
    t = _koga_log()
    assert len(t) == 3
    for row in t.rows():
        assert set(row) == set(TurnLog.FIELDS)
    assert [r["turn"] for r in t.rows()] == [1, 2, 3]
    t.rows()[0]["actor"] = "tampered"
    assert t.rows()[0]["actor"] == "me"


def test_free_hits_counts_switch_ins_and_item_turns_not_attacks():
    t = _koga_log()
    assert t.free_hits() == 2
    assert [r["turn"] for r in t.free_hit_rows()] == [1, 2]
    kinds = [r["action"][0] for r in t.free_hit_rows()]
    assert kinds == ["switch", "item"]


def test_an_attack_that_lands_nothing_while_we_bleed_is_a_free_hit():
    t = TurnLog()
    t.record(actor="me", action=("attack", 0), enemy_hp_before=60,
             enemy_hp_after=60, my_hp_before=40, my_hp_after=22,
             note="missed")
    t.record(actor="enemy", action=("attack", 0), enemy_hp_before=60,
             enemy_hp_after=60, my_hp_before=22, my_hp_after=10)
    assert t.free_hits() == 1     # ours only; the enemy's own row never is


def test_summary_is_one_line_per_turn_and_flags_the_free_hits():
    t = _koga_log()
    lines = t.summary().splitlines()
    assert len(lines) == 3
    assert t.summary().count("\n") == 2
    assert lines[0].startswith("T1 me switch:3")
    assert "FREE HIT" in lines[0] and "FREE HIT" in lines[1]
    assert "FREE HIT" not in lines[2]
    assert "60->12" in lines[2]
    assert all(re.match(r"^T\d ", ln) for ln in lines)


def test_turn_context_manager_snapshots_hp_around_the_action():
    """The plumbing path: a live Battle in, before/after vitals recorded
    without the caller reading WRAM twice by hand."""
    emu = make_emu()
    b = Battle(emu, make_names(), make_bdata())
    t = TurnLog()
    with t.turn(b, actor="me") as rec:
        emu.poke("wEnemyMonHP", (18).to_bytes(2, "big"))
        emu.poke("wBattleMonHP", (30).to_bytes(2, "big"))
        rec.action = ("attack", 1)
        rec.note = "SURF"
    row = t.rows()[0]
    assert (row["enemy_hp_before"], row["enemy_hp_after"]) == (60, 18)
    assert (row["my_hp_before"], row["my_hp_after"]) == (44, 30)
    assert row["enemy_species"] == 95
    assert t.free_hits() == 0


def test_turn_context_manager_records_the_row_even_when_the_turn_raises():
    emu = make_emu()
    b = Battle(emu, make_names(), make_bdata())
    t = TurnLog()
    with pytest.raises(RuntimeError):
        with t.turn(b, actor="me") as rec:
            rec.action = ("switch", 3)
            emu.poke("wBattleMonHP", (20).to_bytes(2, "big"))
            raise RuntimeError("menu misfired")
    row = t.rows()[0]
    assert "menu misfired" in row["note"]
    assert (row["my_hp_before"], row["my_hp_after"]) == (44, 20)
    assert t.free_hits() == 1      # a switch that ceded the turn


# -- DecisionRequired --------------------------------------------------------

def test_decision_required_carries_the_frame_and_the_options():
    f = make_frame()
    err = DecisionRequired("wild ONIX: decide", frame=f, kind="encounter",
                           options=("ko", "catch", "flee"))
    assert isinstance(err, RuntimeError)
    assert err.kind == "encounter"
    assert err.options == ("ko", "catch", "flee")
    assert err.frame["enemy"]["species"] == "ONIX"
    assert "wild ONIX" in str(err)


# -- pre-init frame: the encounter hook fires before the mon block loads -----

def test_frame_stands_in_the_active_party_mon_before_battle_init():
    """Live bug: the encounter hook ran at T0, the battle mon block still read
    back L0 0/0, and a level-comparing disposition policy fled a winnable
    L34 Graveler. The roster is always real -- stand in with its active mon."""
    emu = make_emu()
    # blank the battle mon block the way the engine leaves it pre-init
    emu.poke("wBattleMonSpecies", 0)
    emu.poke("wBattleMonLevel", 0)
    emu.poke("wBattleMonHP", (0).to_bytes(2, "big"))
    emu.poke("wBattleMonMaxHP", (0).to_bytes(2, "big"))
    frame = battle_frame(emu, make_names(), make_bdata())
    assert frame["me"]["level"] == 30          # BROOK from the roster, not 0
    assert frame["me"]["nickname"] == "BROOK"
    assert frame["me"]["max_hp"] == 80
    # slot 1 is standing in, so it is not offered as a switch target
    assert 1 not in frame["can_switch"]
    assert 3 in frame["can_switch"]            # MOO still switchable


def test_frame_leaves_a_populated_battle_mon_alone():
    frame = make_frame(my_hp=44)
    assert frame["me"]["hp"] == 44             # mid-battle HP, not roster HP
    assert frame["me"]["level"] == 30
