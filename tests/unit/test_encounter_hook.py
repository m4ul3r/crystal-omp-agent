"""wren pt6: the MODEL decides battles, the harness reports.

Live evidence these lock down:
  * ~80 wild encounters in one run, ~78 auto-KO'd -- the model was never
    asked 'KO / catch / flee' (d.encounter_policy, consulted ONCE per
    wild, never for a trainer);
  * a "pacing" loop reported fights=0 while the harness silently fought
    ~20 battles (the one 'auto:' warning naming the harness's pick);
  * a ping-pong switch policy fed Koga ~10 free switch-in hits with NO
    per-turn record to diagnose it from (d.last_battle + free_hits);
  * model policies returning None constantly, and the harness quietly
    playing best-damage anyway (require_decision / d.decide_all ->
    DecisionRequired carrying the decision frame).

Duck-typed fakes only: Driver.__new__ plus a scripted Battle whose play()
calls the wrapped policy once per turn, exactly like Battle.play does.
"""
import logging

import pytest

import crystalagent.driver.battle as battle_owner
import crystalagent.driver.world as world_owner
from crystalagent.driver import Driver, DecisionRequired

pytestmark = pytest.mark.unit

# canned decision frame: this test file pins trek's frame builder so the
# plumbing (who gets a frame, what DecisionRequired carries) is checked
# without a WRAM read. The frame's own shape is decide.py's contract.
FRAME = {
    "me": {"nickname": "GATOR", "species": "FERALIGATR", "level": 40,
           "hp": 90, "max_hp": 120, "types": [11], "status": []},
    "enemy": {"nickname": "KOFFING", "species": "KOFFING", "level": 37,
              "hp": 70, "max_hp": 70, "types": [7], "status": []},
    "party": [], "bag": {"GREAT BALL": 5}, "turn": 0, "wild": True,
    "can_switch": [1, 2],
    "moves": [{"slot": 0, "name": "SURF", "type": 11, "power": 95,
               "pp": 15, "effect_mult": 1.0}],
}

MOVE_NAMES = {13: "SURF", 21: "BITE"}


class FakeEmu:
    def __init__(self, mode=1):
        self.frame = 0
        self.rows = [" " * 20 for _ in range(18)]
        self.u8 = {"wBattleMode": mode}

    def tick(self, n=1):
        self.frame += n

    def screen_text(self):
        return list(self.rows)

    def read_u8(self, sym):
        return self.u8.get(sym, 0)


class FakeNames:
    moves = MOVE_NAMES
    species = {1: "FERALIGATR"}
    maps = {}


class FakeBattle:
    """Battle.play's contract, scripted: one policy call per turn, the
    action executed on a toy world, HP drifting so the per-turn record has
    something to record."""

    def __init__(self, emu, turns=1, catch_after=1, default="attack"):
        self.emu = emu
        self.turns = turns
        self.catch_after = catch_after
        self.default = default
        self.actions = []
        self.balls = []
        self.switches = []
        self.fled = False
        self.outcome = "won"
        self.my_hp = 90
        self.enemy_hp = 70

    # -- snapshots ------------------------------------------------------
    def me(self):
        return {"species": 1, "name": "FERALIGATR", "nickname": "GATOR",
                "party_slot": 0, "level": 40, "hp": self.my_hp,
                "max_hp": 120, "types": [11], "status": [],
                "moves": [(13, 15), (21, 25)]}

    def enemy(self):
        return {"species": 109, "name": "KOFFING", "nickname": "KOFFING",
                "party_slot": 0, "level": 37, "hp": self.enemy_hp,
                "max_hp": 70, "types": [7], "status": []}

    def best_move(self):
        return 0

    def _default_policy(self, me, enemy, potion_frac):
        return self.default

    # -- the loop -------------------------------------------------------
    def play(self, policy=None, max_frames=0, want_nickname=False,
             text_handler=None):
        for _ in range(self.turns):
            self.emu.tick(60)
            act = policy(self.emu.screen_text(), self.me(),
                         self.enemy()) if policy else None
            self.actions.append(act)
            kind = act[0] if isinstance(act, tuple) and act else act
            arg = act[1] if isinstance(act, tuple) and len(act) > 1 else None
            if kind == "flee":
                self.fled = True
                self.outcome = "fled"
                break
            if kind == "ball":
                self.balls.append(arg)
                if len(self.balls) >= self.catch_after:
                    self.outcome = "caught"
                    break
                continue
            if kind == "switch":
                self.switches.append(arg)
                self.my_hp -= 20        # the free hit a switch-in eats
                continue
            self.enemy_hp = max(self.enemy_hp - 25, 0)
            self.my_hp = max(self.my_hp - 5, 0)
        self.emu.u8["wBattleMode"] = 0
        return self.outcome


def battle_driver(monkeypatch, mode=1, turns=1, hook=None, catch_after=1,
                  default="attack", bag=("POKE BALL", "GREAT BALL"),
                  frame=FRAME):
    """A Driver whose real fight()/catch() drive a FakeBattle."""
    d = Driver.__new__(Driver)
    d.emu = FakeEmu(mode)
    d.names = FakeNames()
    d.bdata = None
    d.state_path = None
    d.whiteouts = 0
    d._whiteout_pending = False
    d._pending_nickname = None
    d.default_policy = None
    d.move_changes = []
    d.encounter_events = []
    if hook is not None:
        d.encounter_policy = hook
    d._resolve_learn_flow = lambda *a, **k: None
    d._party_moves = lambda: []
    d._diff_learned_moves = lambda *a, **k: None
    d.flush_dialog = lambda *a, **k: None
    d.map_name = lambda: "ROUTE_38"

    fake = FakeBattle(d.emu, turns=turns, catch_after=catch_after,
                      default=default)
    monkeypatch.setattr(battle_owner, "Battle", lambda *a, **k: fake)
    state = {
        "player": {"money": 3000},
        "party": [{"species": "FERALIGATR", "hp": 90, "max_hp": 120,
                   "egg": False}],
    }
    monkeypatch.setattr(battle_owner, "game_state", lambda *a: state)
    monkeypatch.setattr(world_owner, "game_state", lambda *a: state)
    monkeypatch.setattr(battle_owner, "bag_item_index",
                        lambda emu, names, item, pocket="items":
                        0 if item in bag else None)
    monkeypatch.setattr(battle_owner, "battle_frame",
                        (lambda b: frame) if frame is not None else None)
    return d, fake


def warnings_with(caplog, needle):
    return [r.getMessage() for r in caplog.records
            if r.levelno >= logging.WARNING and needle in r.getMessage()]


def actions(fake):
    return fake.actions


# -- the encounter question: asked ONCE, wilds only --------------------------

def test_wild_consults_encounter_policy_exactly_once(monkeypatch):
    seen = []

    def hook(frame):
        seen.append(frame)
        return "ko"

    d, fake = battle_driver(monkeypatch, mode=1, turns=3, hook=hook)
    d.fight()
    assert len(seen) == 1                 # ONE question per encounter
    assert seen[0] is FRAME               # ... asked with the frame
    assert len(fake.actions) == 3         # ... and the battle played out
    assert all(a == ("attack", 0) for a in fake.actions)


def test_trainer_battle_never_consults_encounter_policy(monkeypatch):
    seen = []
    d, fake = battle_driver(monkeypatch, mode=2, turns=2,
                            hook=lambda frame: seen.append(frame) or "flee")
    d.fight()
    assert seen == []                     # nothing to decide about a trainer
    assert fake.fled is False
    assert d.encounter_events[-1]["wild"] is False
    assert d.encounter_events[-1]["disposition"] is None


def test_disposition_flee_takes_the_run_path(monkeypatch):
    d, fake = battle_driver(monkeypatch, turns=3, hook=lambda f: "flee")
    d.fight()
    assert fake.actions == ["flee"]
    assert fake.fled is True
    assert d.encounter_events[-1]["disposition"] == "flee"


def test_disposition_ball_throws_the_named_ball(monkeypatch):
    d, fake = battle_driver(monkeypatch, turns=4,
                            hook=lambda f: ("ball", "GREAT BALL"),
                            catch_after=2)
    d.fight()
    assert fake.balls == ["GREAT BALL", "GREAT BALL"]
    assert fake.outcome == "caught"
    assert d.encounter_events[-1]["disposition"] == "catch:GREAT BALL"


def test_bare_catch_uses_cheapest_ball_in_the_pocket(monkeypatch):
    d, fake = battle_driver(monkeypatch, turns=2, hook=lambda f: "catch",
                            bag=("GREAT BALL", "ULTRA BALL"))
    d.fight()
    assert fake.balls == ["GREAT BALL"]    # never the ULTRA BALL


def test_catch_disposition_flees_when_the_pocket_is_dry(monkeypatch):
    d, fake = battle_driver(monkeypatch, turns=2, hook=lambda f: "catch",
                            bag=())
    d.fight()
    assert fake.balls == []
    assert fake.actions == ["flee"]        # never KO the target instead


def test_disposition_ko_keeps_the_callers_policy(monkeypatch):
    d, fake = battle_driver(monkeypatch, turns=2, hook=lambda f: "ko")
    d.fight(policy=lambda rows, me, enemy: ("attack", 1))
    assert fake.actions == [("attack", 1), ("attack", 1)]


def test_catch_does_not_re_ask_the_encounter_policy(monkeypatch):
    seen = []
    d, fake = battle_driver(monkeypatch, turns=2,
                            hook=lambda f: seen.append(f) or "flee")
    d.catch(ball="POKE BALL")
    assert seen == []                     # catch() IS the disposition
    assert fake.balls == ["POKE BALL"]


def test_unknown_disposition_warns_and_kos(monkeypatch, caplog):
    with caplog.at_level(logging.WARNING, logger="trek"):
        d, fake = battle_driver(monkeypatch, turns=1,
                                hook=lambda f: "run away!")
        d.fight()
    assert len(warnings_with(caplog, "encounter_policy answered")) == 1
    assert fake.actions == [("attack", 0)]


def test_raising_encounter_policy_warns_and_kos(monkeypatch, caplog):
    def hook(frame):
        raise ValueError("boom")

    with caplog.at_level(logging.WARNING, logger="trek"):
        d, fake = battle_driver(monkeypatch, turns=1, hook=hook)
        d.fight()
    assert len(warnings_with(caplog, "encounter_policy raised")) == 1
    assert fake.actions == [("attack", 0)]


def test_legacy_triple_encounter_policy_still_works(monkeypatch):
    """No decide module (frame=None): the hook gets rows/me/enemy."""
    seen = []

    def hook(rows, me, enemy):
        seen.append((len(rows), me["nickname"], enemy["name"]))
        return "flee"

    d, fake = battle_driver(monkeypatch, turns=2, hook=hook, frame=None)
    d.fight()
    assert seen == [(18, "GATOR", "KOFFING")]
    assert fake.fled is True


# -- require_decision / decide_all: the harness refuses to pick --------------

def test_require_decision_raises_with_the_frame_attached(monkeypatch):
    d, fake = battle_driver(monkeypatch, mode=2, turns=3)
    with pytest.raises(DecisionRequired) as err:
        d.fight(policy=lambda rows, me, enemy: None, require_decision=True)
    assert err.value.frame is FRAME
    assert err.value.kind == "turn"
    assert fake.actions == []              # the turn never executed


def test_decide_all_raises_on_a_turn_with_no_policy(monkeypatch):
    d, fake = battle_driver(monkeypatch, mode=2, turns=2)
    d.decide_all = True
    with pytest.raises(DecisionRequired) as err:
        d.fight()
    assert err.value.frame is FRAME
    assert err.value.kind == "turn"


def test_decide_all_raises_for_a_wild_with_no_encounter_policy(monkeypatch):
    d, fake = battle_driver(monkeypatch, mode=1, turns=2)
    d.decide_all = True
    with pytest.raises(DecisionRequired) as err:
        d.fight()
    assert err.value.kind == "encounter"      # asked BEFORE any turn
    assert err.value.frame is FRAME
    assert fake.actions == []


def test_decide_all_encounter_policy_returning_none_raises(monkeypatch):
    d, fake = battle_driver(monkeypatch, mode=1, turns=2,
                            hook=lambda f: None)
    d.decide_all = True
    with pytest.raises(DecisionRequired) as err:
        d.fight()
    assert err.value.kind == "encounter"


def test_require_decision_is_satisfied_by_a_real_decision(monkeypatch):
    d, fake = battle_driver(monkeypatch, mode=2, turns=2)
    d.fight(policy=lambda rows, me, enemy: ("attack", 1),
            require_decision=True)
    assert fake.actions == [("attack", 1), ("attack", 1)]


def test_encounter_policy_none_return_kos_by_default(monkeypatch):
    """Without decide_all, an unanswered encounter keeps the old behaviour
    (play it out) -- no new failure mode for existing callers."""
    d, fake = battle_driver(monkeypatch, turns=2, hook=lambda f: None)
    d.fight()
    assert fake.actions == [("attack", 0), ("attack", 0)]
    assert d.encounter_events[-1]["disposition"] is None


# -- the per-turn record ----------------------------------------------------

def test_last_battle_records_one_row_per_turn(monkeypatch):
    d, fake = battle_driver(monkeypatch, mode=2, turns=3)
    d.fight(policy=lambda rows, me, enemy: ("attack", 1))
    rows = d.last_battle.rows() if hasattr(d.last_battle, "rows") \
        else d.last_battle
    assert [r["turn"] for r in rows] == [1, 2, 3]
    assert [r["action"] for r in rows] == [("attack", 1)] * 3
    assert [r["note"] for r in rows] == ["policy"] * 3
    assert rows[0]["enemy_species"] == "KOFFING"
    # before/after HP so a turn's cost is visible after the fact
    assert rows[0]["enemy_hp_before"] == 70
    assert rows[0]["enemy_hp_after"] == 45
    assert rows[-1]["my_hp_after"] is not None


def test_free_hits_counts_switch_ins_and_says_so_once(monkeypatch, caplog):
    """The Koga wipe: ~10 free switch-in hits, invisible. Now one line."""
    with caplog.at_level(logging.WARNING, logger="trek"):
        d, fake = battle_driver(monkeypatch, mode=2, turns=4)
        d.fight(policy=lambda rows, me, enemy: ("switch", 1))
    assert fake.switches == [1, 1, 1, 1]
    loud = warnings_with(caplog, "free_hits=4")
    assert len(loud) == 1
    assert "4 turns" in loud[0]
    assert d.encounter_events[-1]["free_hits"] == 4


def test_two_switch_ins_stay_quiet(monkeypatch, caplog):
    with caplog.at_level(logging.WARNING, logger="trek"):
        d, fake = battle_driver(monkeypatch, mode=2, turns=2)
        d.fight(policy=lambda rows, me, enemy: ("switch", 1))
    assert warnings_with(caplog, "free_hits") == []
    assert d.encounter_events[-1]["free_hits"] == 2




# -- silent auto-play is over ----------------------------------------------

def test_no_policy_emits_exactly_one_auto_warning(monkeypatch, caplog):
    with caplog.at_level(logging.INFO, logger="trek"):
        d, fake = battle_driver(monkeypatch, mode=2, turns=3)
        d.fight()
    autos = warnings_with(caplog, "auto:")
    assert len(autos) == 1
    assert "attack slot 0 (SURF)" in autos[0]
    assert "HARNESS" in autos[0]
    assert d.encounter_events[-1]["decided"] == 0


def test_auto_warning_names_a_flee_the_harness_chose(monkeypatch, caplog):
    with caplog.at_level(logging.WARNING, logger="trek"):
        d, fake = battle_driver(monkeypatch, mode=2, turns=1,
                                default="flee")
        d.fight()
    autos = warnings_with(caplog, "auto:")
    assert len(autos) == 1
    assert "auto: flee" in autos[0]


def test_ball_throws_are_ceded_turns_but_not_a_warning(monkeypatch, caplog):
    """A 4-ball catch cedes 4 turns (decide.TurnLog counts them) but is
    not the Koga class: no loud line, or the warning cries wolf."""
    with caplog.at_level(logging.WARNING, logger="trek"):
        d, fake = battle_driver(monkeypatch, turns=4, hook=lambda f: "catch",
                                catch_after=4)
        d.fight()
    assert fake.balls == ["POKE BALL"] * 4
    assert warnings_with(caplog, "free_hits") == []
    ev = d.encounter_events[-1]
    assert ev["free_hits"] == 0 and ev["ceded_turns"] == 4


def test_declining_policy_is_info_not_a_warning(monkeypatch, caplog):
    """A steered battle whose policy passes on a turn is not the silent
    auto-play class: it gets one INFO line, no warning."""
    with caplog.at_level(logging.INFO, logger="trek"):
        d, fake = battle_driver(monkeypatch, mode=2, turns=2)
        d.fight(policy=lambda rows, me, enemy: None)
    assert warnings_with(caplog, "auto:") == []
    infos = [r.getMessage() for r in caplog.records
             if "auto:" in r.getMessage()]
    assert len(infos) == 1
    assert "policy declined" in infos[0]


def test_default_policy_attribute_still_steers(monkeypatch, caplog):
    with caplog.at_level(logging.WARNING, logger="trek"):
        d, fake = battle_driver(monkeypatch, mode=2, turns=2)
        d.default_policy = lambda rows, me, enemy: ("attack", 1)
        d.fight()
    assert fake.actions == [("attack", 1), ("attack", 1)]
    assert warnings_with(caplog, "auto:") == []


# -- frame-shaped turn policies --------------------------------------------

def test_single_argument_turn_policy_is_handed_the_frame(monkeypatch):
    seen = []

    def policy(frame):
        seen.append(frame)
        return ("attack", 1)

    d, fake = battle_driver(monkeypatch, mode=2, turns=2)
    d.fight(policy=policy)
    assert seen == [FRAME, FRAME]
    assert fake.actions == [("attack", 1), ("attack", 1)]
    assert d.last_frame is FRAME


def test_battle_frame_helper_returns_none_outside_battle(monkeypatch):
    d, fake = battle_driver(monkeypatch, mode=0)
    assert d.battle_frame() is None
    d.emu.u8["wBattleMode"] = 1
    assert d.battle_frame() is FRAME


# -- glue to the REAL crystalagent.decide.battle_frame ----------------------

class FakeSym:
    def offset(self, a, b):
        return 0

    def __getitem__(self, label):
        return (0, 0)


def test_encounter_hook_gets_the_real_decide_frame(monkeypatch):
    """No patched frame builder: fight() -> _frame(b) -> the actual
    crystalagent.decide.battle_frame(b), whose dict is what the hook sees.
    Proves the single-positional Battle call shape, not just the plumbing.
    """
    seen = []
    d, fake = battle_driver(monkeypatch, turns=1,
                            hook=lambda frame: seen.append(frame) or "flee",
                            frame=None)
    monkeypatch.undo()          # restore the real battle_owner.battle_frame
    monkeypatch.setattr(battle_owner, "Battle", lambda *a, **k: fake)
    state = {
        "player": {"money": 3000},
        "party": [{"species": "FERALIGATR", "hp": 90, "max_hp": 120,
                   "egg": False}],
    }
    monkeypatch.setattr(battle_owner, "game_state", lambda *a: state)
    monkeypatch.setattr(world_owner, "game_state", lambda *a: state)
    # what decide.battle_frame reads beyond me()/enemy(): pockets, party
    # count, turn counter
    fake.names = d.names
    fake.data = None
    d.emu.u8.update({"wNumItems": 0, "wNumBalls": 1, "wPartyCount": 0,
                     "wPlayerTurnsTaken": 3})
    d.emu.sym = FakeSym()
    d.emu.read = lambda where, n=1: bytes([4, 5][:n])
    d.names.items = {4: "GREAT BALL"}
    d.fight()
    assert len(seen) == 1
    frame = seen[0]
    assert set(frame) == {"me", "enemy", "party", "bag", "turn", "wild",
                          "can_switch", "moves"}
    assert frame["wild"] is True and frame["turn"] == 3
    assert frame["me"]["nickname"] == "GATOR"
    assert frame["enemy"]["species"] == "KOFFING"
    assert [m["name"] for m in frame["moves"]] == ["SURF", "BITE"]
    assert frame["bag"].get("GREAT BALL") == 5
    # each consult re-reads: last_frame is the latest one, same content
    assert d.last_frame == frame
    assert fake.fled is True    # the disposition still drives the battle
