"""A leader that already beat this party must not be re-challenged.

Lead level alone is a bad predictor of a gym: this run walked an L53 MIGHTYENA
into Wallace three separate times and lost every one, because the other five
sat at 42-49 with nothing super-effective. Each loss halves the money, and the
money is what buys the balls the Pokedex needs -- the run drained to 62.
"""

import json

import pytest

from pokeagent import quest


class _Quest:
    """Just enough of Objectives for the loss bookkeeping."""

    def __init__(self, tmp_path, levels):
        self.d = type("D", (), {"state_path": str(tmp_path / "run.state")})()
        self._levels = levels

    def party(self):
        return [type("M", (), {"level": n})() for n in self._levels]

    # the methods under test
    _loss_path = quest.Quest._loss_path
    _losses = quest.Quest._losses
    party_total = quest.Quest.party_total
    note_loss = quest.Quest.note_loss
    retry_bar = quest.Quest.retry_bar


@pytest.mark.unit
def test_no_bar_before_any_loss(tmp_path):
    q = _Quest(tmp_path, [50, 40])
    assert q.retry_bar("WALLACE") is None


@pytest.mark.unit
def test_bar_demands_a_stronger_whole_party(tmp_path):
    q = _Quest(tmp_path, [53, 42, 44, 44, 49, 41])
    total = q.party_total()
    q.note_loss("WALLACE")

    bar = q.retry_bar("WALLACE")
    assert bar == total + quest.RETRY_PARTY_GAIN
    assert bar > total, "a rematch at the same strength must be refused"

    # Levelling only the lead by one is NOT enough to clear the bar.
    q._levels = [54, 42, 44, 44, 49, 41]
    assert q.party_total() < bar

    # Spreading real levels across the party does clear it.
    q._levels = [55, 45, 46, 46, 51, 44]
    assert q.party_total() >= bar


@pytest.mark.unit
def test_losses_are_per_leader_and_persist(tmp_path):
    q = _Quest(tmp_path, [30, 30])
    q.note_loss("NORMAN")
    assert q.retry_bar("WALLACE") is None
    assert q.retry_bar("NORMAN") is not None

    reloaded = _Quest(tmp_path, [30, 30])
    assert reloaded.retry_bar("NORMAN") == q.retry_bar("NORMAN")
    assert json.loads(reloaded._loss_path().read_text())["NORMAN"] == 60


@pytest.mark.unit
def test_leader_name_case_does_not_disarm_the_bar(tmp_path):
    """The quest tables say "Wallace"; journals and callers say "WALLACE".

    A case mismatch silently returned None here, so the run kept walking into
    a gym it had already lost -- which is the exact bug this guard exists for.
    """
    q = _Quest(tmp_path, [40, 40, 40])
    q.note_loss("WALLACE")
    assert q.retry_bar("Wallace") is not None
    assert q.retry_bar("wallace") == q.retry_bar("WALLACE")
