"""Buying, against a real shop.

The mart is the one place in the harness where a wrong press SPENDS money, and
the predecessor project's journal records both failure directions: blind A
presses buying single items at 200 a time, and the confirmation box going
unanswered so purchases silently never happened while the code reported
success. So this lane checks the money and the bag, never a message.

It needs a savestate standing at a counter, which `saves/stone-badge.state`
plus a short walk provides.
"""

import shutil

import pytest

from pokeagent.mart import Mart
from pokeagent.trek import Driver, TravelInterrupted

pytestmark = pytest.mark.integration

MART = "OldaleTown_Mart"
CLERK = (1, 3)


@pytest.fixture(scope="module")
def counter_state(tmp_path_factory):
    """Walk to the counter ONCE and keep the savestate.

    Each test still gets its own Driver loaded from this file, so they stay
    independent -- one test buys, another checks a refusal does not spend --
    but the eighty-second walk happens once instead of six times.
    """
    from pathlib import Path

    src = Path("saves/stone-badge.state")
    if not src.exists():
        pytest.skip("no stone-badge milestone to walk from")
    work = tmp_path_factory.mktemp("mart") / "walk.state"
    shutil.copy2(src, work)
    shutil.copy2(str(src) + ".meta", str(work) + ".meta")
    d = Driver(str(work))
    for _ in range(14):
        if d.map_name() == MART:
            break
        try:
            d.travel(MART, on_battle="fight")
        except TravelInterrupted:
            d.fight()
            d.advance_scene(30000)
        except Exception:  # noqa: BLE001 - a leg may need another pass
            d.advance_scene(20000)
    if d.map_name() != MART:
        pytest.skip(f"could not reach {MART}")
    d.save(str(work))
    return str(work)


@pytest.fixture()
def at_the_counter(counter_state, tmp_path):
    """A fresh driver at the counter, with the shop open."""
    state = tmp_path / "mart.state"
    shutil.copy2(counter_state, state)
    shutil.copy2(counter_state + ".meta", str(state) + ".meta")
    d = Driver(str(state))
    d.talk_to(*CLERK)
    d.settle(120)
    mart = Mart(d)
    for _ in range(4):
        if mart.is_open():
            break
        d.emu.run_sequence("A:4 .:40")
    if not mart.is_open():
        pytest.skip("the clerk did not open a shop")
    return d, mart


def test_the_stock_comes_from_the_shop_itself(at_the_counter):
    """Read out of gMartInfo.itemList, so it is what this mart really sells
    rather than a table someone typed."""
    _, mart = at_the_counter
    names = {row["name"] for row in mart.items()}
    assert "POKé BALL" in names
    for row in mart.items():
        assert row["price"] > 0, f"{row['name']} has no price"


def test_the_selected_row_is_known_exactly(at_the_counter):
    """itemList[choicesAbove + cursor] IS the highlight, so the driver can say
    what it is about to buy instead of counting rows on a screen."""
    _, mart = at_the_counter
    assert mart.enter_buy()
    assert mart.select("POKé BALL")
    assert (mart.selected() or {}).get("name") == "POKé BALL"


def test_a_purchase_moves_the_bag_and_the_wallet(at_the_counter):
    d, mart = at_the_counter
    before_money = d.state.money()
    before_balls = mart._bag_count(4)

    assert mart.buy("POKé BALL", 5) is True, mart.last_reason

    after_balls = mart._bag_count(4)
    after_money = d.state.money()
    assert after_balls - before_balls == 5, "asked for five, count must agree"
    assert before_money - after_money == 5 * 200, "price must be exact"


def test_an_unaffordable_purchase_is_refused_before_pressing_anything(
    at_the_counter,
):
    """A shop that cannot complete leaves a modal box open, and an open box
    eats every movement input afterwards."""
    d, mart = at_the_counter
    money_before = d.state.money()
    assert mart.buy("POKé BALL", 999) is False
    assert "costs" in mart.last_reason
    assert d.state.money() == money_before, "a refusal must not spend"


def test_an_item_the_mart_does_not_sell_is_refused(at_the_counter):
    _, mart = at_the_counter
    assert mart.buy("MASTER BALL", 1) is False
    assert "not sold here" in mart.last_reason


def test_leaving_hands_movement_back(at_the_counter):
    """The menus come down before the field-control lock does; an impatient
    exit left a run standing at the counter for an hour."""
    d, mart = at_the_counter
    assert mart.leave() is True
    assert not d.scene_active()
    assert d.step_dir("D") is True
