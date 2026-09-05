"""teach_tm names a mon: verification must be scoped to THAT party row.

Live cost: STRENGTH already sat on DUCK, so teaching it to EMBER broke
out of the forget-walk on its first check and reported 'learned' without
pressing a single button -- EMBER went into Chuck's gym with the same
resisted moveset that had just wiped the party.
"""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
from crystalagent.driver import Driver  # noqa: E402

pytestmark = pytest.mark.unit


class Walker(Driver):
    """Party skeleton: slot 0 knows nothing, slot 1 knows STRENGTH."""

    def __init__(self):
        self.presses = []
        self.party = [
            {"nickname": "EMBER", "moves": [{"name": "CUT"}]},
            {"nickname": "DUCK", "moves": [{"name": "STRENGTH"}]},
        ]

    def observe(self):
        return {"party": self.party}

    def press(self, seq):
        self.presses.append(seq)
        # the third press is when the game actually writes the move
        if len(self.presses) >= 3:
            self.party[0]["moves"].append({"name": "STRENGTH"})

    def textbox(self):
        return False

    def cursor_rows(self):
        return []

    class _Emu:
        @staticmethod
        def screen_text():
            return [""]

    emu = _Emu()


def test_slot_scope_ignores_another_mons_copy_of_the_move():
    d = Walker()
    assert d._party_knows("STRENGTH")[0] is True        # party-wide: yes
    assert d._party_knows("STRENGTH", slot=0)[0] is False


def test_walk_presses_when_the_named_slot_lacks_the_move():
    d = Walker()
    assert d._walk_forget_menu("STRENGTH", slot=0) is True
    assert d.presses, "the walk must press something for the named mon"
    assert d.party[0]["moves"][-1]["name"] == "STRENGTH"


def test_party_wide_walk_still_short_circuits():
    d = Walker()
    assert d._walk_forget_menu("STRENGTH") is True
    assert d.presses == []


class Lister(Walker):
    """The learner knows four, slot 1 is an HM move (the live shape)."""

    def __init__(self, screens, forget_effect=True):
        super().__init__()
        self.party[0]["moves"] = [{"name": n} for n in
                                  ("CUT", "FLAME WHEEL", "FURY CUTTER",
                                   "EMBER")]
        self.screens = list(screens)
        self.forget_effect = forget_effect
        self.downs = 0
        self.confirmed = False

    def press(self, seq):
        self.presses.append(seq)
        if seq.startswith("D"):
            self.downs += 1
        elif seq.startswith("A") and self.screens and \
                "forgotten?" in "".join(self.screens[0]).lower():
            self.confirmed = True
            if self.forget_effect:
                self.party[0]["moves"][3] = {"name": "STRENGTH"}
        if self.screens:
            self.screens.pop(0)

    class _Emu:
        rows = [""]

        def screen_text(self):
            return self.rows

    def __getattribute__(self, k):
        return object.__getattribute__(self, k)

    @property
    def emu(self):
        e = Lister._Emu()
        e.rows = self.screens[0] if self.screens else [""]
        return e


def test_forget_row_skips_hm_moves_by_default():
    d = Lister([])
    assert d._forget_row(None, 0) == 1        # CUT is an HM move
    assert d._forget_row("EMBER", 0) == 3


def test_walk_targets_the_named_move_row():
    screens = [["Which move should", "be forgotten?"]] * 4
    d = Lister(screens)
    assert d._walk_forget_menu("STRENGTH", "EMBER", slot=0) is True
    assert d.downs == 3, "must walk to the 4th row, not press A on CUT"
    assert d.confirmed
