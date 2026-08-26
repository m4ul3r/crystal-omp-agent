"""Navigation must never spend money, and if it does, it must be loud.

Live cost: an A-mash beside a Poke Mart clerk bought ¥1200 of ESCAPE
ROPEs, ¥200 a press, and nothing noticed until the wallet was read hours
later (AGENTS.md gotcha 13). Clerk identity does not exist at runtime --
`object_event` sprite ids are parsed by the map tooling and never reach
WRAM -- so the guard watches the SYMPTOM: the wallet, around every
movement entry point (goto, walk, pace).
"""
import logging

import pytest

from trek import Driver

pytestmark = pytest.mark.unit


class WalletEmu:
    """Emu whose money changes on a scripted schedule of reads."""

    def __init__(self, wallet):
        self.frame = 0
        self.wallet = list(wallet)      # consumed one read at a time
        self.reads = 0
        self.rows = [" " * 20 for _ in range(18)]

    def tick(self, n=1):
        self.frame += n

    def screen_text(self):
        return list(self.rows)

    def read_u8(self, sym):
        return 0

    def read_be(self, sym, n):
        assert sym == "wMoney"
        val = self.wallet[min(self.reads, len(self.wallet) - 1)]
        self.reads += 1
        return val


def driver(wallet):
    d = Driver.__new__(Driver)
    d.emu = WalletEmu(wallet)
    d.map_name = lambda: "GOLDENROD_CITY"
    d.pos = lambda: (1, 2, 9, 12)
    d.press = lambda seq: None
    d.textbox = lambda: False
    return d


def test_a_purchase_during_a_goto_is_reported(caplog):
    d = driver([13000, 11800])          # ¥1200 gone across the call
    d._goto_walk = lambda *a, **k: True
    with caplog.at_level(logging.WARNING, logger="trek"):
        assert d.goto(3, 4) is True
    assert d.last_money_delta == -1200
    text = caplog.text
    assert "MONEY -1200" in text
    assert "GOLDENROD_CITY" in text and "(9, 12)" in text


def test_a_purchase_during_a_walk_is_reported(caplog):
    d = driver([500, 300])
    d._step = lambda mv: "moved"
    with caplog.at_level(logging.WARNING, logger="trek"):
        assert d.walk("R") is True
    assert d.last_money_delta == -200


def test_an_unchanged_wallet_says_nothing(caplog):
    d = driver([13000, 13000])
    d._goto_walk = lambda *a, **k: True
    with caplog.at_level(logging.WARNING, logger="trek"):
        assert d.goto(3, 4) is True
    assert d.last_money_delta == 0
    assert "MONEY" not in caplog.text


def test_prize_money_is_recorded_but_never_warns(caplog):
    """The warning is about SPENDING. Trainer winnings arrive mid-walk all
    the time, and `MONEY +216 ... movement must never spend money` was a
    false alarm that taught the reader to ignore the line -- so the delta
    is still recorded, and nothing is logged."""
    d = driver([500, 1500])
    d._goto_walk = lambda *a, **k: True
    with caplog.at_level(logging.WARNING, logger="trek"):
        d.goto(1, 1)
    assert d.last_money_delta == 1000
    assert "MONEY" not in caplog.text


def test_a_wallet_the_fake_cannot_read_is_not_an_error():
    """Duck-typed drivers in tests have no wallet; the guard must not turn
    that into a navigation failure."""
    d = driver([0])

    class NoWallet(WalletEmu):
        def read_be(self, sym, n):
            raise KeyError(sym)

    d.emu = NoWallet([0])
    d._goto_walk = lambda *a, **k: True
    assert d.goto(2, 2) is True
    assert d.last_money_delta == 0
