"""claude-wren leg 3 battle guards: impossible policy actions (switch to a
fainted/EGG/missing slot, item/ball not in bag, dry attack slot) substituted
with the default action after ONE warning, repeated-invalid degradation to
plain attacks within two turns, and the frozen-screen wedge cap (structured
'wedged' outcome, diagnostic printed at most twice)."""
import logging
from types import SimpleNamespace

import pytest

from crystalagent.battle import Battle

pytestmark = pytest.mark.unit

MENU_ROWS = ["GATOR  L18", "", "FIGHT  PKMN", "PACK   RUN"]
FROZEN_ROWS = ["GATOR", "", "...blinking cursor that will never advance"]


class FakeEmu:
    def __init__(self, rows):
        self.frame = 0
        self.rows = rows

    def screen_text(self):
        return list(self.rows)

    def read_u8(self, name):
        assert name == "wCurBattleMon", name
        return 0


class FakeMenu:
    """Same shape as Menus.wait_for: tick fake frames until pred/timeout."""

    def __init__(self, emu):
        self.emu = emu
        self.presses = []

    def press(self, seq):
        self.presses.append(seq)
        self.emu.frame += 10

    def wait_for(self, predicate, timeout_frames=600, quiet=False):
        start = self.emu.frame
        while self.emu.frame - start < timeout_frames:
            if predicate(self.emu.screen_text()):
                return True
            self.emu.frame += 10
        return False


class Harness(Battle):
    """Battle whose emu-touching primitives are faked; play(), the policy
    validation, and the wedge detection all run for real."""

    default_act = "attack"

    def __init__(self, rows=MENU_ROWS, turns=2, bag=("POTION",),
                 alive=(0,), count=3, eggs=()):
        self.emu = FakeEmu(rows)
        self.menu = FakeMenu(self.emu)
        self.names = SimpleNamespace(moves={}, species={})
        self.turns_left = turns
        self.bag = {n: i for i, n in enumerate(bag)}
        self.alive = list(alive)
        self.count = count
        self.eggs = set(eggs)
        self.executed = []
        self._me = {"species": 159, "name": "GATOR", "level": 18,
                    "hp": 20, "max_hp": 24, "types": [1],
                    "moves": [(10, 5), (33, 0)]}   # slot 1 is dry
        self._enemy = {"species": 16, "name": "PIDGEY", "level": 5,
                       "hp": 12, "max_hp": 12, "types": [0]}

    # -- observation fakes -------------------------------------------------
    def active(self):
        return self.turns_left > 0

    def me(self):
        return dict(self._me)

    def enemy(self):
        return dict(self._enemy)

    def party_alive(self):
        return True

    def _party_count(self):
        return min(self.count, 6)

    def _alive_slots(self):
        return list(self.alive)

    def _egg_slots(self):
        return set(self.eggs)

    def bag_item_index(self, name, pocket="items"):
        return self.bag.get(name)

    def _default_policy(self, me, enemy, potion_frac):
        return self.default_act

    # -- action fakes: succeed, advance one turn, move the vitals ----------
    def _turn(self, record):
        self.executed.append(record)
        self.turns_left -= 1
        self._enemy["hp"] -= 1    # vitals move: no false freeze fingerprint
        return True

    def attack(self, move_idx=None):
        return self._turn(("attack", move_idx))

    def use_battle_item(self, name, target_slot=0):
        return self._turn(("item", name))

    def switch_to(self, i):
        return self._turn(("switch", i))

    def throw_ball(self, name="POKE BALL"):
        return self._turn(("ball", name))

    def flee(self):
        return self._turn(("flee", None))


def run(harness, policy, caplog):
    with caplog.at_level(logging.WARNING, logger="trek"):
        outcome = harness.play(policy=policy)
    warnings = [r.getMessage() for r in caplog.records
                if "impossible" in r.getMessage()]
    return outcome, warnings


# -- (a) impossible switch substituted, single warning ------------------------

def test_switch_to_fainted_substituted_with_default(caplog):
    """The live GATOR wedge: ('switch', 2) to a fainted mon every turn.
    Substituted with the default attack, warned exactly once."""
    h = Harness(alive=[0], count=3)
    outcome, warnings = run(h, lambda rows, me, enemy: ("switch", 2), caplog)
    assert outcome == "won"
    assert all(kind == "attack" for kind, _ in h.executed)
    assert not any(kind == "switch" for kind, _ in h.executed)
    assert len(warnings) == 1 and "fainted" in warnings[0]


@pytest.mark.parametrize("act,harness_kw,reason", [
    (("switch", 5), dict(count=3), "out of party range"),
    (("switch", 1), dict(alive=[0, 1], eggs=[1]), "EGG"),
    (("attack", 1), dict(), "no PP"),          # slot 1 has 0 PP
    (("attack", 7), dict(), "out of range"),
    (("ball", "MASTER BALL"), dict(), "not in bag"),
])
def test_impossible_actions_substituted(act, harness_kw, reason, caplog):
    h = Harness(**harness_kw)
    outcome, warnings = run(h, lambda rows, me, enemy: act, caplog)
    assert outcome == "won"
    # never executed as asked: every executed turn is the substituted default
    assert h.executed and all(kind == "attack" and arg is None
                              for kind, arg in h.executed)
    assert len(warnings) == 1 and reason in warnings[0]


# -- (b) item not in bag -------------------------------------------------------

def test_item_not_in_bag_substituted(caplog):
    h = Harness(bag=("POTION",))
    outcome, warnings = run(h, lambda rows, me, enemy: ("item", "X ATTACK"),
                            caplog)
    assert outcome == "won"
    assert not any(kind == "item" for kind, _ in h.executed)
    assert all(kind == "attack" for kind, _ in h.executed)
    assert len(warnings) == 1 and "not in bag" in warnings[0]


# -- (c) repeated invalid policy degrades to pure default within 2 turns ------

def test_repeated_invalid_policy_degrades_within_two_turns(caplog):
    """Each substitution counts as a fail: turn 1 runs the default policy's
    own pick, turn 2 the fails>=2 wedge guard is already forcing plain
    attacks -- the policy's output no longer steers anything."""
    h = Harness()
    h.default_act = ("item", "POTION")        # in the bag: valid default
    calls = []

    def policy(rows, me, enemy):
        calls.append(1)
        return ("switch", 2)                  # fainted: invalid every turn

    outcome, warnings = run(h, policy, caplog)
    assert outcome == "won"
    assert h.executed == [("item", "POTION"), ("attack", None)]
    assert len(calls) == 2                    # once per turn, never re-asked
    assert len(warnings) == 1                 # identical mistake: one line


def test_valid_policy_action_still_executes(caplog):
    h = Harness()
    outcome, warnings = run(h, lambda rows, me, enemy: ("attack", 0), caplog)
    assert outcome == "won"
    assert h.executed == [("attack", 0), ("attack", 0)]
    assert warnings == []


# -- (d) frozen screen: diagnostic capped, structured 'wedged' outcome --------

def test_frozen_screen_returns_wedged_with_capped_diagnostic(caplog):
    """Text and vitals frozen across passes: one full diagnostic, one
    recovery attempt, one 'suppressing' line, then a structured 'wedged'
    return instead of the historic 200+-line unbounded loop."""
    h = Harness(rows=FROZEN_ROWS, turns=999)
    with caplog.at_level(logging.WARNING, logger="trek"):
        outcome = h.play()
    assert outcome == "wedged"
    msgs = [r.getMessage() for r in caplog.records]
    frozen = [m for m in msgs if "frozen screen" in m]
    suppress = [m for m in msgs if "suppressing further identical" in m]
    assert len(frozen) == 1
    assert len(suppress) == 1
    # the recovery re-sync was attempted exactly once before bailing
    assert h.menu.presses.count("B:4 .:12") == 1


def test_frozen_screen_never_loops_to_frame_cap():
    """'wedged' lands long before the 120k frame cap: the wedge cap is a
    bounded number of confirm windows, not the caller's frame budget."""
    h = Harness(rows=FROZEN_ROWS, turns=999)
    h.play()
    assert h.emu.frame < 5000


def test_moving_screen_never_flags_wedge(caplog):
    """A normal battle (vitals move every turn) never trips the freeze
    detector or prints a diagnostic."""
    h = Harness(turns=4)
    with caplog.at_level(logging.WARNING, logger="trek"):
        outcome = h.play()
    assert outcome == "won"
    assert not any("frozen screen" in r.getMessage() for r in caplog.records)
