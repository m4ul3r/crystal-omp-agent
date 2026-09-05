"""Declining a level-up move must dismiss BOTH boxes.

Gen 3 asks twice -- "Delete an older move?" then "Give up on learning X?" --
and answering only the first leaves the engine looping back to the first one
forever. A grind logged "harness declined THIEF for MIGHTYENA" thousands of
times while the party gained nothing at all.
"""

import pytest


class _Recorder:
    """Minimal stand-in: the prompt clears only after a YES follows the NO."""

    def __init__(self, clear_after_yes=True):
        self.answers = []
        self.clear_after_yes = clear_after_yes
        self.cleared = False
        self.sequences = []

    # --- the two calls _decline_learn makes -------------------------------
    def _answer_yes_no(self, yes):
        self.answers.append(bool(yes))
        if yes and self.clear_after_yes:
            self.cleared = True
        return True

    def learn_prompt(self):
        return None if self.cleared else {"new_move": {"name": "THIEF"}}

    class _Emu:
        def __init__(self, outer):
            self.outer = outer

        def run_sequence(self, seq):
            self.outer.sequences.append(seq)

    @property
    def emu(self):
        return _Recorder._Emu(self)


def _decline(rec):
    from pokeagent.battle import BattleSession

    BattleSession._decline_learn(rec)


@pytest.mark.unit
def test_decline_answers_no_then_yes():
    rec = _Recorder()
    _decline(rec)
    assert rec.answers[0] is False, "the first box must be answered NO"
    assert True in rec.answers[1:], (
        "the 'give up on learning' confirmation must be answered YES; "
        f"got {rec.answers}"
    )
    assert rec.learn_prompt() is None


@pytest.mark.unit
def test_decline_is_bounded_when_the_prompt_never_clears():
    """A prompt that refuses to go away must not spin forever."""
    rec = _Recorder(clear_after_yes=False)
    _decline(rec)
    assert len(rec.answers) <= 8, f"unbounded retry loop: {len(rec.answers)}"
