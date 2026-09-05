"""ROM-free combat decisions against real outlook and action dispatch paths."""

from types import SimpleNamespace

import pytest

from pokeagent import cstruct
from pokeagent.battle import BattleSession, SAFARI_BALL, SAFARI_GO_NEAR, SAFARI_RUN
from pokeagent.catching import Catcher
from pokeagent.cconst import Constants
from pokeagent.tactics import Tactics, norm_item

pytestmark = pytest.mark.unit


class Memory:
    """Only the RAM/input boundary is simulated; tactics reads real mon bytes."""

    def __init__(self, layout, flags):
        self.frame = 0
        self.layout = layout
        self.flags = flags
        self.mons = [bytearray(0x58), bytearray(0x58)]
        self.charmap = SimpleNamespace(decode=lambda raw: raw.split(b"\0")[0].decode())
        self.on_press = None

    def populate(self, index, species, *, pp=0):
        raw = self.mons[index]
        for key, value in {
            "species": species, "hp": 100, "maxHP": 100,
            "attack": 20, "defense": 20, "speed": 20,
            "spAttack": 20, "spDefense": 20, "moves": 1,
        }.items():
            off = self.layout[key]
            raw[off:off + 2] = value.to_bytes(2, "little")
        raw[self.layout["level"]] = 10
        raw[self.layout["pp"]] = pp
        off = self.layout["statStages"]
        raw[off:off + 8] = bytes([6] * 8)

    def read(self, address, size):
        name, offset = address
        assert name == "gBattleMons"
        return bytes(self.mons[offset // 0x58][:size])

    def u16(self, address):
        return self.flags if address == "gBattleTypeFlags" else 0

    def u8(self, address):
        return 0

    def tick(self, frames):
        self.frame += frames

    def run_sequence(self, sequence):
        self.tick(12)
        if self.on_press:
            self.on_press()


class Tables:
    type_chart = {}

    def __init__(self, items):
        self.items = items
        self.prices = {
            items["ITEM_MASTER_BALL"]: 0,
            items["ITEM_POKE_BALL"]: 200,
            items["ITEM_GREAT_BALL"]: 600,
        }

    def species(self, species):
        return f"MON{species}"

    def type(self, type_id):
        return "NORMAL"

    def ability(self, ability_id):
        return "NONE"

    def base_stats(self, species):
        return SimpleNamespace(type1=0, type2=0)

    def move(self, move_id):
        return "TACKLE"

    def move_data(self, move_id):
        return SimpleNamespace(name="TACKLE", type=0, power=40, accuracy=100,
                               effect=0, priority=0)

    def item_data(self, item_id):
        return SimpleNamespace(price=self.prices[item_id], pocket=2)


class State:
    def __init__(self, safari):
        self.safari = safari
        self.active = True
        self.balls = {"MASTER BALL": 1, "GREAT BALL": 2, "POKé BALL": 2}
        self.zone_balls = 30
        self.mons = []

    def in_battle(self):
        return self.active

    def battle(self):
        if not self.active:
            return SimpleNamespace(kinds=())
        return SimpleNamespace(kinds=("safari",) if self.safari else ("wild",))

    def party(self):
        return self.mons

    def bag(self):
        return {"poke_balls": dict(self.balls)}

    def flag(self, name):
        return False

    def safari_balls(self):
        return self.zone_balls


def make_tactics(*, safari=False, blank=False):
    consts = Constants()
    t = Tactics.__new__(Tactics)
    t.consts = consts
    t.b = consts.battle
    t.items = consts.items
    t.T = consts.ns("pokemon.h")
    t.abilities = consts.ns("abilities.h")
    t.holds = consts.ns("hold_effects.h")
    t.move_effects = consts.ns("battle_move_effects.h")
    t.item_fx = consts.ns("item_effects.h")
    t.min_stage, t.default_stage, t.max_stage = 0, 6, 12
    t.type_mystery = t.T["TYPE_MYSTERY"]
    t.stat_stage_ratios = [(1, 1)] * 13
    t.accuracy_stage_ratios = [(1, 1)] * 13
    t.hold_effect_to_type = {}
    t._type_names = {}
    t._item_ids = {
        norm_item(name): consts.items[key] for name, key in (
            ("MASTER BALL", "ITEM_MASTER_BALL"),
            ("POKé BALL", "ITEM_POKE_BALL"),
            ("GREAT BALL", "ITEM_GREAT_BALL"),
        )
    }
    t.names = Tables(t.items)
    t.state = State(safari)
    t.battle_mon = cstruct.layout("BattlePokemon", "pokemon.h")
    t.battler_stride = 0x58
    t.emu = Memory(t.battle_mon, t.b["BATTLE_TYPE_SAFARI"] if safari else 0)
    if not safari and not blank:
        t.emu.populate(0, 1)
    t.emu.populate(1, 2, pp=10)
    return t


def make_session(monkeypatch, tactics):
    s = BattleSession.__new__(BattleSession)
    s.tactics, s.emu, s.state = tactics, tactics.emu, tactics.state
    s.names, s.consts, s.b = tactics.names, tactics.consts, tactics.b
    s.outcome_names = {}
    s.last_reason = None
    s.last_action_detail = ""
    s._futile = set()
    for method in ("at_learn_prompt", "naming_open", "at_move_menu", "at_party_menu"):
        monkeypatch.setattr(s, method, lambda: False)
    monkeypatch.setattr(s, "at_action_menu", lambda: s.active())
    monkeypatch.setattr(s, "at_safari_menu", lambda: s.state.safari and s.active())
    return s


def party_mon(species, *, hp=100, egg=False, pp=10):
    return SimpleNamespace(species=species, nickname="", level=10, hp=hp,
                           max_hp=100, is_egg=egg, moves=(1,), pp=(pp,),
                           status_name="OK")


def test_dry_lead_switches_using_actual_outlook_contract():
    t = make_tactics()
    t.state.mons = [party_mon(1), party_mon(3, hp=0), party_mon(4, egg=True),
                    party_mon(5, pp=0), party_mon(6)]
    analysis = t.outlook()
    # The live battler is dry even if the party copy still reports PP.
    assert analysis["moves_by_slot"][0]["pp"] == 0
    assert {m["index"] for m in t.switch_options(analysis)} == {3, 4}
    action, _ = t.recommend(analysis)
    assert action == ("switch", 4)
    t.state.mons[-1].hp = 0
    action, _ = t.recommend(t.outlook())
    assert action == "flee"


def test_safari_play_approaches_then_keeps_throwing_without_combat_outlook(monkeypatch):
    t = make_tactics(safari=True)
    s = make_session(monkeypatch, t)
    # A healthy party must not become a fictional player-side battle mon.
    t.state.mons = [party_mon(1)]
    assert t.outlook() is None
    catcher = Catcher(SimpleNamespace(state=t.state, battle=s, names=t.names), object())
    choices = []

    def choose(cursor, label):
        choices.append(cursor)
        t.emu.tick(12)
        if cursor == SAFARI_BALL:
            t.state.zone_balls -= 1
            if t.state.zone_balls == 24:
                t.state.active = False
        return True

    monkeypatch.setattr(s, "_choose_action", choose)
    result = s.play(policy=catcher.policy(object()), max_frames=20_000)
    assert choices == [SAFARI_GO_NEAR] + [SAFARI_BALL] * 6
    assert result["outcome"] == "ended"
    assert all(turn.my_hp_before == turn.my_hp_after == 0 for turn in result["turns"])
    assert all(turn.their_mon == "MON2" for turn in result["turns"])


@pytest.mark.parametrize("policy", [None, lambda frame: None, lambda frame: "flee"])
def test_safari_play_can_leave_without_normal_analysis(monkeypatch, policy):
    t = make_tactics(safari=True)
    s = make_session(monkeypatch, t)
    choices = []

    def choose(cursor, label):
        choices.append(cursor)
        t.state.active = False
        return True

    monkeypatch.setattr(s, "_choose_action", choose)
    result = s.play(policy=policy, max_frames=32)
    assert choices == [SAFARI_RUN]
    assert result["turns"][0].action == "flee"


def test_ordinary_blank_player_still_waits_before_policy(monkeypatch):
    t = make_tactics(blank=True)
    s = make_session(monkeypatch, t)
    calls = []
    result = s.play(policy=lambda frame: calls.append(frame) or "flee", max_frames=16)
    assert result["outcome"] == "timeout"
    assert calls == []


def test_automatic_ball_selection_preserves_master_but_explicit_throw_spends_it(monkeypatch):
    t = make_tactics()
    s = make_session(monkeypatch, t)
    catcher = Catcher(SimpleNamespace(state=t.state, battle=s, names=t.names), object())
    selected = []
    monkeypatch.setattr(s, "_open_bag", lambda: True)
    monkeypatch.setattr(s, "_bag_quantity", lambda item_id: sum(
        quantity for name, quantity in t.state.balls.items()
        if t.item_id(name) == item_id
    ))

    def drive(item_id, name):
        selected.append(name)

        def consume():
            key = next(key for key in t.state.balls if t.item_id(key) == item_id)
            t.state.balls[key] -= 1
            t.emu.on_press = None

        t.emu.on_press = consume
        return True

    monkeypatch.setattr(s, "_drive_bag", drive)
    assert catcher._pick_ball() == "POKé BALL"
    assert s.throw_ball()
    assert selected == ["POKé BALL"]
    assert t.state.balls["MASTER BALL"] == 1
    t.state.balls = {"MASTER BALL": 1, "POKé BALL": 0}
    assert catcher._pick_ball() is None
    assert not s.throw_ball()
    assert selected == ["POKé BALL"]
    assert s.throw_ball("master-ball")
    assert t.state.balls["MASTER BALL"] == 0
    assert selected == ["POKé BALL", "master-ball"]
