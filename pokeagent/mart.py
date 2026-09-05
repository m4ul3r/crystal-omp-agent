"""Buying things, driven off the shop's own state rather than the screen.

The run stalled at one Pokemon with an empty ball pocket and 5,130 in the
wallet: catching works, and nothing could buy what catching spends. This is
the missing half.

Everything here reads `gMartInfo` (src/shop.h:28-38), which the engine keeps
at a fixed address while a shop is open:

    itemList     the items this mart sells, in order
    itemCount    how many
    cursor       the on-screen row, NOT the item index
    choicesAbove how many rows have scrolled off the top

`itemList[choicesAbove + cursor]` is therefore the highlighted item, exactly,
and the driver can say what it is about to buy instead of counting rows on a
decoded screen. That matters more here than anywhere else in the harness: the
predecessor's journal records blind A presses buying single items at 200 a
time, and the "N ITEM(S) will be Y." YES/NO box going unanswered so purchases
silently never happened at all.

Every purchase is verified against the BAG COUNT and the WALLET, not against a
message, and the driver refuses before pressing anything it cannot afford.
"""

from __future__ import annotations

import logging

from . import cstruct

log = logging.getLogger(__name__)

#: Rows the buy list shows at once (src/shop.c:306).
VISIBLE_ROWS = 8

#: Give up driving a menu after this many presses; something is wrong.
MAX_PRESSES = 40


class Mart:
    """A Poke Mart counter, driven through `gMartInfo`."""

    def __init__(self, driver):
        self.d = driver
        self.emu = driver.emu
        self.state = driver.state
        self.names = driver.names
        self.layout = cstruct.layout("MartInfo", "shop.h")
        self.last_reason = ""

    # ---- reading the shop ------------------------------------------------

    @property
    def base(self):
        return self.emu.resolve("gMartInfo")

    def _u8(self, field) -> int:
        return self.emu.u8(self.base + self.layout[field])

    def _u32(self, field) -> int:
        raw = self.emu.read(self.base + self.layout[field], 4)
        return int.from_bytes(raw, "little")

    def is_open(self) -> bool:
        """True while the shop actually owns the screen.

        `itemCount` alone is NOT the test: it survives the shop closing (it is
        only rebuilt when the next mart is created, src/shop.c:123), so a
        count-only predicate reported the shop as open forever after leaving.
        `leave()` then pressed B at an overworld that did not care and the loop
        sat in the Rustboro Mart burning 80k frames a minute.

        The honest test is the count PLUS the field-control lock: while a shop
        is up the player cannot walk.
        """
        try:
            count = self._u8("itemCount")
            if not (0 < count < 64 and self._u32("itemList") > 0x02000000):
                return False
            return bool(self.d.scene_active())
        except Exception:  # noqa: BLE001
            return False

    def items(self) -> list[dict]:
        """Everything this mart sells, with prices from the ROM's own table."""
        if not self.is_open():
            return []
        ptr = self._u32("itemList")
        count = self._u8("itemCount")
        out = []
        for i in range(count):
            item_id = int.from_bytes(self.emu.read(ptr + i * 2, 2), "little")
            if not item_id:
                continue
            try:
                data = self.names.item_data(item_id)
                name, price = self.names.item(item_id), data.price
            except Exception:  # noqa: BLE001
                name, price = f"item {item_id}", 0
            out.append({"index": i, "id": item_id, "name": name, "price": price})
        return out

    def selected(self) -> dict | None:
        """The item the cursor is on: itemList[choicesAbove + cursor]."""
        if not self.is_open():
            return None
        idx = self._u8("choicesAbove") + self._u8("cursor")
        for row in self.items():
            if row["index"] == idx:
                return row
        return None

    # ---- driving it ------------------------------------------------------

    def _fail(self, reason: str) -> bool:
        self.last_reason = reason
        log.warning("[mart] %s", reason)
        return False

    #: The task that owns the scrolling ITEM list. Talking to a clerk opens a
    #: BUY / SELL / EXIT menu first, and the item list only exists after BUY
    #: is chosen -- cursor presses before that drive the wrong menu, which is
    #: how the first purchase attempt moved nothing and bought nothing.
    ITEM_LIST_TASK = "Shop_DoCursorAction"

    def at_item_list(self) -> bool:
        try:
            return any(t.startswith(self.ITEM_LIST_TASK)
                       for t in self.state.tasks())
        except Exception:  # noqa: BLE001
            return False

    def enter_buy(self) -> bool:
        """Choose BUY and wait for the item list to actually own the screen."""
        if self.at_item_list():
            return True
        for _ in range(8):
            self.emu.run_sequence("A:4 .:40")
            if self.at_item_list():
                return True
        return self._fail("chose BUY but the item list never opened")

    def select(self, name: str) -> bool:
        """Move the cursor onto a named item, verifying by index each step."""
        want = name.upper()
        target = next(
            (r for r in self.items() if r["name"].upper() == want), None
        )
        if target is None:
            sold = ", ".join(r["name"] for r in self.items())
            return self._fail(f"{name} is not sold here (stock: {sold})")
        for _ in range(MAX_PRESSES):
            here = self._u8("choicesAbove") + self._u8("cursor")
            if here == target["index"]:
                return True
            key = "DOWN" if target["index"] > here else "UP"
            self.emu.run_sequence(f"{key}:4 .:10")
        return self._fail(
            f"could not move the cursor onto {name} "
            f"(stuck at {self._u8('choicesAbove') + self._u8('cursor')})"
        )

    def buy(self, name: str, qty: int = 1) -> bool:
        """Buy `qty` of `name`. Verified by the bag and the wallet.

        Refuses before pressing anything it cannot pay for, because a shop
        that cannot complete leaves a modal box open and an open box eats
        every movement input afterwards.
        """
        self.last_reason = ""
        if not self.is_open():
            return self._fail("no mart is open")
        row = next(
            (r for r in self.items() if r["name"].upper() == name.upper()), None
        )
        if row is None:
            return self._fail(f"{name} is not sold here")

        cost = row["price"] * qty
        money = self.state.money()
        if cost > money:
            afford = money // row["price"] if row["price"] else 0
            return self._fail(
                f"{qty}x {name} costs {cost} and we have {money} "
                f"(affordable: {afford})"
            )

        before_money = money
        before_count = self._bag_count(row["id"])

        if not self.enter_buy():
            return False
        if not self.select(name):
            return False

        # Open the quantity box, then raise it one press at a time. Held input
        # overshoots here and the confirm spends the difference.
        # The quantity box does NOT open on the first A -- the engine walks a
        # couple of message tasks first and only then hands input to
        # Shop_PrintPrice (src/shop.c:751). Pressing UP before that goes
        # nowhere, which is why a request for ten balls bought exactly one.
        if not self._wait_for_quantity_box():
            return self._fail("the quantity box never opened")

        # Raise it one press at a time, VERIFYING against the engine's own
        # counter rather than trusting the presses: tItemCount is data[1] of
        # the Shop_PrintPrice task (src/shop.c:509).
        for _ in range(MAX_PRESSES):
            have = self.quantity()
            if have is None or have >= qty:
                break
            before = have
            self.emu.run_sequence("UP:4 .:12")
            if self.quantity() == before:
                break          # the box has stopped rising: out of money
        got = self.quantity()
        if got is not None and got != qty:
            log.info("[mart] quantity settled at %d of %d requested "
                     "(the box caps at what the wallet allows)", got, qty)
        self.emu.run_sequence("A:4 .:30")

        # From here the engine walks a chain of message tasks with exactly one
        # decision in it -- "N ITEM(S) will be Y. Okay?" on
        # Task_CallYesOrNoCallback. That box is answered DELIBERATELY; the
        # rest are text. Nothing answered it in the predecessor project, so
        # purchases silently never happened while the code claimed success.
        answered = False
        for _ in range(MAX_PRESSES):
            if self._at_yes_no():
                self.d.resolve_choice("YES")
                answered = True
                continue
            if self._bag_count(row["id"]) > before_count and \
                    self.state.money() < before_money:
                break
            self.emu.run_sequence("A:4 .:24")
        self.d.settle(90)

        after_count = self._bag_count(row["id"])
        after_money = self.state.money()
        gained = after_count - before_count
        spent = before_money - after_money
        if gained <= 0:
            return self._fail(
                f"pressed through the {name} purchase but the bag still holds "
                f"{after_count} (money {before_money} -> {after_money}; "
                f"yes/no {'answered' if answered else 'never appeared'})"
            )
        if spent != gained * row["price"]:
            log.warning(
                "[mart] bought %dx %s but spent %d, not %d -- check the "
                "quantity box", gained, name, spent, gained * row["price"],
            )
        log.info(
            "[mart] bought %dx %s for %d (bag %d -> %d, money %d -> %d)",
            gained, name, spent, before_count, after_count,
            before_money, after_money,
        )
        return True

    #: The task that owns the "how many?" box and its price readout.
    QUANTITY_TASK = "Shop_PrintPrice"

    def quantity(self) -> int | None:
        """The engine's own item counter, or None when the box is closed."""
        try:
            data = self.state.task_data(self.QUANTITY_TASK)
        except Exception:  # noqa: BLE001
            return None
        if not data or len(data) < 2:
            return None
        return int(data[1])

    def _wait_for_quantity_box(self) -> bool:
        for _ in range(12):
            if self.quantity() is not None:
                return True
            self.emu.run_sequence("A:4 .:24")
        return self.quantity() is not None

    def _at_yes_no(self) -> bool:
        """True while the purchase confirmation box owns input."""
        try:
            return any(t.startswith("Task_CallYesOrNoCallback")
                       for t in self.state.tasks())
        except Exception:  # noqa: BLE001
            return False

    def _bag_count(self, item_id: int) -> int:
        try:
            name = self.names.item(item_id)
        except Exception:  # noqa: BLE001
            return 0
        for pocket in self.state.bag().values():
            if isinstance(pocket, dict) and name in pocket:
                return int(pocket[name])
        return 0

    def leave(self) -> bool:
        """Close the shop with B only -- A here spends money.

        Patient on purpose: the menus come down quickly but the field-control
        lock is released by a fade several presses later. Measured, B1 put the
        overworld tasks back and B8 finally handed movement over, so an
        eight-press budget stopped exactly one press short and the loop then
        sat in the Rustboro Mart for the rest of the run.
        """
        for _ in range(16):
            if not self.is_open() and not self.d.scene_active():
                return True
            self.emu.run_sequence("B:4 .:24")
        return not self.d.scene_active()
