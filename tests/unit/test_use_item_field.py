"""Out-of-battle item use (session claude-wren pt10).

Live failure this covers: `Driver.use_item(name, target_slot=N)` returned
False with the bag untouched, while the SAME items worked through the
in-battle item action -- alternating between success and failure across
identical calls (REVIVE ok, HYPER POTION ok, FULL RESTORE fail, FULL HEAL
ok, HYPER POTION fail, FULL RESTORE fail).

The root cause, found by driving two calls from byte-identical
savestates: `Menus.select_label('PACK')` confirms a row with a 2-frame A
and reports success from the CURSOR GLYPH alone, so on the frames right
after the START menu is drawn (its input loop is not running yet --
AGENTS.md gotcha 2) that A is swallowed. The pack never opened,
`goto_pocket` burned its budget on wJumptableIndex 128 (the START menu),
and use_item returned False with no log line at all -- leaving the START
menu OPEN, which eats the caller's next input (gotcha 7), so the next
call's START press merely closed it again.

Covered here: the swallowed-confirm retry, a clean field on failure,
bidirectional party targeting (up AND down), nickname targeting, the
engine's own "It won't have any effect." no-op as a distinct outcome, and
heal_party's cheapest-sufficient item choice.
"""
import pytest

import trek
from trek import (Driver, _field_clear, _no_effect_message,
                  _party_target_list, _norm_item)
from crystalagent.battle import cheapest_heal

pytestmark = pytest.mark.unit


class FakeEmu:
    def __init__(self):
        self.frame = 0
        self.rows = [" " * 20 for _ in range(18)]
        self.u8 = {}

    def tick(self, n=1):
        self.frame += n

    def screen_text(self):
        return list(self.rows)

    def read_u8(self, sym):
        return self.u8.get(sym, 0)


def bare_driver():
    d = Driver.__new__(Driver)
    d.emu = FakeEmu()
    d.names = None
    d.settle = lambda **kw: None
    d.textbox = lambda: False
    d.flush_dialog = lambda *a, **k: "done"
    return d


def screen(*lines):
    rows = [l.ljust(20)[:20] for l in lines]
    return rows + [" " * 20] * (18 - len(rows))


# -- screen predicates -------------------------------------------------------

def test_target_list_needs_hp_fractions_not_just_cancel():
    """The item pocket draws its OWN CANCEL row once scrolled to the
    bottom. Accepting that as the "Use on which PM?" list let the party
    steering run while the POCKET's cursor was still live in
    wMenuCursorY, and fired an A at the wrong screen."""
    assert not _party_target_list(screen("  FULL RESTORE   × 2",
                                         "  HYPER POTION   × 4",
                                         " CANCEL"))
    assert _party_target_list(screen("   BROOK      74/211",
                                     "   GATOR     293/293",
                                     " CANCEL"))
    # the prompt alone is enough, before any row is painted
    assert _party_target_list(screen("Use on which ᴾᴹ?"))


def test_no_effect_message_matches_the_engine_text():
    """_ItemWontHaveEffectText, split over two lines as the box draws it
    (data/text/common_3.asm), with the charmap's apostrophe glyph."""
    assert _no_effect_message(screen("It wonᵗ have any", "effect."))
    assert _no_effect_message(screen("It won't have any", "effect."))
    # item description panels must never look like a refusal
    assert not _no_effect_message(screen("Restores POKéMON", "HP by 200."))
    assert not _no_effect_message(screen("Cures poisoned", "POKéMON."))


def test_field_clear_sees_every_modal_layer():
    assert _field_clear(screen("WREN", "New BARK TOWN"))
    assert not _field_clear(screen(" ▶PACK", " EXIT"))
    assert not _field_clear(screen(" CANCEL"))


# -- _party_target: bidirectional, like battle.py's _party_row_select --------

def party_menu_driver(start_row, rows=6):
    """Party target list up with its cursor PERSISTED on `start_row`
    (0-based): InitPartyMenuWithCancel restores wPartyMenuCursor into
    wMenuCursorY, so a fresh open never starts at the top."""
    d = bare_driver()
    state = {"confirmed": [], "ups": 0, "downs": 0}
    d.emu.u8["wMenuCursorY"] = start_row + 1

    def press(seq):
        d.emu.tick(5)
        if seq.startswith("U"):
            state["ups"] += 1
            d.emu.u8["wMenuCursorY"] = max(1, d.emu.u8["wMenuCursorY"] - 1)
        elif seq.startswith("D"):
            state["downs"] += 1
            d.emu.u8["wMenuCursorY"] = min(rows, d.emu.u8["wMenuCursorY"] + 1)
        elif seq.startswith("A"):
            state["confirmed"].append(d.emu.u8["wMenuCursorY"] - 1)

    d.press = press
    return d, state


def test_party_target_climbs_up_to_slot_zero():
    """The live repro: an item was just used on a LOWER row, so the menu
    reopens there. A DOWN-only walk can never reach slot 0."""
    d, state = party_menu_driver(start_row=5)
    assert d._party_target(0) is True
    assert state["confirmed"] == [0]
    assert (state["ups"], state["downs"]) == (5, 0)


def test_party_target_walks_down_to_the_last_slot():
    d, state = party_menu_driver(start_row=0)
    assert d._party_target(5) is True
    assert state["confirmed"] == [5]
    assert (state["ups"], state["downs"]) == (0, 5)


def test_party_target_on_the_persisted_row_confirms_without_moving():
    d, state = party_menu_driver(start_row=2)
    assert d._party_target(2) is True
    assert state["confirmed"] == [2]
    assert (state["ups"], state["downs"]) == (0, 0)


def test_party_target_bails_when_the_cursor_is_pinned():
    """A cursor that stops responding means the wrong menu is up: fail
    fast instead of mashing A at whatever is drawn."""
    d, state = party_menu_driver(start_row=5)
    d.press = lambda seq: d.emu.tick(5)      # presses move nothing
    assert d._party_target(0) is False
    assert state["confirmed"] == []


# -- use_item end to end -----------------------------------------------------

def field_world(d, monkeypatch, *, qty=2, start_row=0, pocket_index=0,
                pack_opens_after=1, consume_row=None, no_effect_rows=()):
    """Wire use_item's field collaborators.

    `pack_opens_after`: how many PACK confirms the START menu swallows
    before the pack actually opens (1 = the healthy case).
    `consume_row`: the only party row whose A consumes the item.
    `no_effect_rows`: rows where the engine answers
    _ItemWontHaveEffectText and consumes nothing.
    """
    world = {"qty": qty, "confirms": 0, "pack_open": False,
             "party_open": False, "a_rows": []}
    monkeypatch.setattr(trek, "bag_item_index", lambda *a, **k: pocket_index)
    monkeypatch.setattr(trek, "bag_quantity", lambda *a, **k: world["qty"])
    monkeypatch.setattr(trek, "goto_pocket",
                        lambda menu, pocket: world["pack_open"])
    d.emu.u8["wMenuCursorY"] = start_row + 1

    def confirm_pack():
        world["confirms"] += 1
        if world["confirms"] >= pack_opens_after:
            world["pack_open"] = True
            d.emu.rows[8] = "  POTION      ×  2".ljust(20)

    class M:
        def select_label(self, label, max_presses=14):
            if label == "PACK":
                confirm_pack()
            elif label == "USE":
                world["party_open"] = True
                d.emu.rows[0] = "   BROOK      74/211"
                d.emu.rows[12] = " CANCEL".ljust(20)
            return True

        def wait_for_label(self, label, timeout_frames=300):
            return True

        def wait_for(self, pred, timeout_frames=600, quiet=False):
            start = d.emu.frame
            while d.emu.frame - start < timeout_frames:
                if pred(d.emu.screen_text()):
                    return True
                d.press(".:4")
            return False

        def scroll_abs(self):
            return pocket_index

        def cursor_row(self):
            return (8, "POTION      ×  2")

    d.menu = M()

    def press(seq):
        d.emu.tick(5)
        if seq.startswith("START"):
            d.emu.rows[5] = "  ▶PACK".ljust(20)
        elif seq.startswith("U"):
            d.emu.u8["wMenuCursorY"] = max(1, d.emu.u8["wMenuCursorY"] - 1)
        elif seq.startswith("D"):
            d.emu.u8["wMenuCursorY"] = min(6, d.emu.u8["wMenuCursorY"] + 1)
        elif seq.startswith("B"):
            d.emu.rows = [" " * 20 for _ in range(18)]     # every layer closes
        elif seq.startswith("A"):
            if not world["pack_open"]:
                confirm_pack()          # the START-menu confirm, retried
            elif world["party_open"]:
                row = d.emu.u8["wMenuCursorY"] - 1
                world["a_rows"].append(row)
                if row in no_effect_rows:
                    d.emu.rows[14] = "It wonᵗ have any".ljust(20)
                    d.emu.rows[16] = "effect.".ljust(20)
                elif consume_row is None or row == consume_row:
                    world["qty"] = max(0, world["qty"] - 1)

    d.press = press
    return world


def test_use_item_survives_a_swallowed_pack_confirm(monkeypatch):
    """THE regression: the first PACK confirm lands while the START menu's
    input loop is still down. The confirm is retried until the pack is
    verifiably open, so the item is still used."""
    d = bare_driver()
    world = field_world(d, monkeypatch, pack_opens_after=2)
    assert d.use_item("POTION") is True
    assert d.last_item_reason == "used"
    assert world["qty"] == 1
    assert world["confirms"] == 2          # one swallowed, one landed


def test_use_item_reports_no_pack_and_leaves_no_stray_menu(monkeypatch):
    """A pack that never opens must say so AND put the field back: the old
    code returned False silently with the START menu still up, which ate
    the caller's next input and made the NEXT call fail too."""
    d = bare_driver()
    world = field_world(d, monkeypatch, pack_opens_after=99)
    assert d.use_item("POTION") is False
    assert d.last_item_reason == "no-pack"
    assert world["qty"] == 2                       # nothing consumed
    assert _field_clear(d.emu.screen_text())       # START menu gone


def test_use_item_targets_slot_zero_after_a_lower_slot(monkeypatch):
    """The exact pt10 sequence: an item was just used on slot 2, so the
    party menu reopens on row 2. Targeting slot 0 must climb, and must
    never confirm the row it started on."""
    d = bare_driver()
    world = field_world(d, monkeypatch, start_row=2, consume_row=0)
    assert d.use_item("POTION", target_slot=0) is True
    assert world["a_rows"] == [0]
    assert world["qty"] == 1


def test_use_item_targets_a_lower_slot_after_slot_zero(monkeypatch):
    d = bare_driver()
    world = field_world(d, monkeypatch, start_row=0, consume_row=5)
    assert d.use_item("POTION", target_slot=5) is True
    assert world["a_rows"] == [5]
    assert world["qty"] == 1


def test_use_item_full_hp_target_is_a_distinct_no_op(monkeypatch):
    """The engine's own refusal (_ItemWontHaveEffectText). Nothing is
    consumed and nothing ever will be, so it gets its own reason and the
    A presses STOP -- mashing on would spend the item on the next mon."""
    d = bare_driver()
    world = field_world(d, monkeypatch, no_effect_rows=(0,))
    assert d.use_item("POTION", target_slot=0) is False
    assert d.last_item_reason == "no-effect"
    assert world["qty"] == 2                # bag untouched
    assert world["a_rows"] == [0]           # exactly one confirm, no mashing


def test_use_item_missing_item_never_opens_a_menu(monkeypatch):
    d = bare_driver()
    field_world(d, monkeypatch)
    monkeypatch.setattr(trek, "bag_item_index", lambda *a, **k: None)
    presses = []
    inner, d.press = d.press, lambda seq: presses.append(seq) or inner(seq)
    assert d.use_item("POTION") is False
    assert d.last_item_reason == "not-in-bag"
    assert presses == []


# -- mon= nickname targeting -------------------------------------------------

PARTY = [{"nickname": "BROOK", "hp": 74, "max_hp": 211, "egg": False},
         {"nickname": "GATOR", "hp": 293, "max_hp": 293, "egg": False},
         {"nickname": "SNAG", "hp": 60, "max_hp": 131, "egg": False}]


def with_party(monkeypatch, party=PARTY):
    monkeypatch.setattr(trek, "game_state",
                        lambda emu, names: {"party": [dict(m) for m in party]})


def test_use_item_resolves_a_nickname_to_its_slot(monkeypatch):
    d = bare_driver()
    with_party(monkeypatch)
    world = field_world(d, monkeypatch, start_row=2, consume_row=0)
    assert d.use_item("POTION", mon="BROOK") is True
    assert world["a_rows"] == [0]


def test_use_item_nickname_is_case_and_space_blind(monkeypatch):
    d = bare_driver()
    with_party(monkeypatch)
    world = field_world(d, monkeypatch, start_row=0, consume_row=2)
    assert d.use_item("POTION", mon=" snag ") is True
    assert world["a_rows"] == [2]


def test_use_item_rejects_an_unknown_nickname(monkeypatch):
    d = bare_driver()
    with_party(monkeypatch)
    field_world(d, monkeypatch)
    with pytest.raises(ValueError) as err:
        d.use_item("POTION", mon="NOBODY")
    assert "NOBODY" in str(err.value)
    assert "BROOK" in str(err.value)        # names the party it did see


def test_use_item_refuses_both_target_slot_and_mon(monkeypatch):
    d = bare_driver()
    with_party(monkeypatch)
    field_world(d, monkeypatch)
    with pytest.raises(ValueError):
        d.use_item("POTION", target_slot=0, mon="BROOK")


def test_use_item_keeps_the_old_positional_call_shape(monkeypatch):
    """Backward compatibility: use_item('POTION', 1) and the default
    slot-0 call must both still work."""
    d = bare_driver()
    world = field_world(d, monkeypatch, start_row=0, consume_row=1)
    assert d.use_item("POTION", 1) is True
    assert world["a_rows"] == [1]


# -- heal_party: cheapest sufficient item ------------------------------------

# Exactly what _load_heal_table reads out of this ROM (see the ROM test
# below): HealingHPAmounts, StatusHealingActions, ItemAttributes prices.
PSN = 0x08
TABLE = {
    "POTION": {"name": "POTION", "hp": 20, "cures": 0,
               "revives": False, "price": 300},
    "SUPERPOTION": {"name": "SUPER POTION", "hp": 50, "cures": 0,
                    "revives": False, "price": 700},
    "HYPERPOTION": {"name": "HYPER POTION", "hp": 200, "cures": 0,
                    "revives": False, "price": 1200},
    "FULLRESTORE": {"name": "FULL RESTORE", "hp": 999, "cures": 0xFF,
                    "revives": False, "price": 3000},
    "FULLHEAL": {"name": "FULL HEAL", "hp": 0, "cures": 0xFF,
                 "revives": False, "price": 600},
    "ANTIDOTE": {"name": "ANTIDOTE", "hp": 0, "cures": PSN,
                 "revives": False, "price": 100},
    "REVIVE": {"name": "REVIVE", "hp": 0, "cures": 0,
               "revives": True, "price": 1500},
}


def cheapest(bag, need_hp=0, status=0, fainted=False, allow=None):
    return cheapest_heal(TABLE, bag, allow, need_hp, status, fainted)


def test_cheapest_heal_never_burns_a_full_restore_on_a_potion_wound():
    bag = {"POTION": 1, "HYPERPOTION": 2, "FULLRESTORE": 2}
    assert cheapest(bag, need_hp=11) == "POTION"


def test_cheapest_heal_takes_the_cheapest_item_that_covers_the_shortfall():
    bag = {"POTION": 1, "SUPERPOTION": 1, "HYPERPOTION": 1, "FULLRESTORE": 1}
    assert cheapest(bag, need_hp=45) == "SUPER POTION"
    assert cheapest(bag, need_hp=137) == "HYPER POTION"


def test_cheapest_heal_prefers_a_covering_item_over_a_partial_one():
    """A FULL RESTORE is the only thing in the bag that covers a 45 HP
    shortfall in ONE use, so it wins even though a POTION is cheaper:
    'cheapest SUFFICIENT'. The POTION-sized-wound case above is the one
    that must never reach for it."""
    assert cheapest({"POTION": 3, "FULLRESTORE": 1},
                    need_hp=45) == "FULL RESTORE"


def test_cheapest_heal_falls_back_to_the_biggest_on_offer():
    """Nothing in the bag covers the whole shortfall: take the biggest
    heal there is and let the caller loop."""
    assert cheapest({"POTION": 3}, need_hp=45) == "POTION"
    assert cheapest({"POTION": 3, "SUPERPOTION": 1}, need_hp=300) == \
        "SUPER POTION"


def test_cheapest_heal_cures_status_with_the_specific_item():
    bag = {"ANTIDOTE": 1, "FULLHEAL": 1, "FULLRESTORE": 1}
    assert cheapest(bag, need_hp=0, status=PSN) == "ANTIDOTE"
    # no ANTIDOTE left: FULL HEAL (600) beats FULL RESTORE (3000)
    assert cheapest({"FULLHEAL": 1, "FULLRESTORE": 1},
                    need_hp=0, status=PSN) == "FULL HEAL"


def test_cheapest_heal_needs_a_revive_for_a_fainted_mon():
    """A POTION on a fainted mon is the engine's "won't have any
    effect" -- only a revive can touch it."""
    assert cheapest({"POTION": 5}, need_hp=99, fainted=True) is None
    assert cheapest({"POTION": 5, "REVIVE": 1}, need_hp=99,
                    fainted=True) == "REVIVE"


def test_cheapest_heal_respects_the_whitelist_and_the_bag():
    bag = {"POTION": 1, "HYPERPOTION": 1}
    assert cheapest(bag, need_hp=11, allow={"HYPERPOTION"}) == "HYPER POTION"
    assert cheapest({}, need_hp=11) is None


def heal_driver(monkeypatch, party, bag, status=None):
    """heal_party with the pack drive faked out: what is under test is
    WHICH item it picks and which mons it leaves alone."""
    d = bare_driver()
    st = dict(status or {})
    used = []
    monkeypatch.setattr(trek, "game_state",
                        lambda emu, names: {"party": [dict(m) for m in party]})
    d._heal_items = lambda: TABLE
    d._bag = lambda: bag
    d._status_byte = lambda slot: st.get(slot, 0)

    def use_item(item_name, target_slot=0, **kw):
        key = _norm_item(item_name)
        assert bag.get(key), f"heal_party spent a {item_name} it had none of"
        bag[key] -= 1
        if not bag[key]:
            del bag[key]
        used.append((target_slot, item_name))
        it, mon = TABLE[key], party[target_slot]
        if it["revives"] and mon["hp"] == 0:
            mon["hp"] = mon["max_hp"] // 2
        if it["cures"]:
            st[target_slot] = st.get(target_slot, 0) & ~it["cures"]
        if it["hp"] and mon["hp"] > 0:
            mon["hp"] = min(mon["max_hp"], mon["hp"] + it["hp"])
        d.last_item_reason = "used"
        return True

    d.use_item = use_item
    return d, used


def mon(nick, hp, max_hp, egg=False):
    return {"nickname": nick, "hp": hp, "max_hp": max_hp, "egg": egg}


def test_heal_party_picks_the_cheapest_sufficient_item(monkeypatch):
    party = [mon("BROOK", 200, 211), mon("GATOR", 293, 293)]
    bag = {"POTION": 1, "HYPERPOTION": 1, "FULLRESTORE": 1}
    d, used = heal_driver(monkeypatch, party, bag)
    out = d.heal_party()
    assert out == {"BROOK": "POTION", "GATOR": "already full"}
    assert used == [(0, "POTION")]
    assert bag == {"HYPERPOTION": 1, "FULLRESTORE": 1}   # never touched


def test_heal_party_leaves_healthy_mons_alone(monkeypatch):
    party = [mon("BROOK", 211, 211), mon("GATOR", 293, 293)]
    bag = {"POTION": 2}
    d, used = heal_driver(monkeypatch, party, bag)
    assert d.heal_party() == {"BROOK": "already full",
                             "GATOR": "already full"}
    assert used == []
    assert bag == {"POTION": 2}


def test_heal_party_cures_status_before_reaching_for_a_full_restore(monkeypatch):
    party = [mon("BROOK", 74, 211)]
    bag = {"ANTIDOTE": 1, "HYPERPOTION": 1, "FULLRESTORE": 1}
    d, used = heal_driver(monkeypatch, party, bag, status={0: PSN})
    assert d.heal_party() == {"BROOK": "ANTIDOTE, HYPER POTION"}
    assert used == [(0, "ANTIDOTE"), (0, "HYPER POTION")]
    assert bag == {"FULLRESTORE": 1}


def test_heal_party_revives_then_tops_up(monkeypatch):
    party = [mon("RIPTIDE", 0, 162)]
    bag = {"REVIVE": 1, "HYPERPOTION": 1, "POTION": 1}
    d, used = heal_driver(monkeypatch, party, bag)
    assert d.heal_party() == {"RIPTIDE": "REVIVE, HYPER POTION"}
    assert used == [(0, "REVIVE"), (0, "HYPER POTION")]


def test_heal_party_stops_when_the_items_run_out(monkeypatch):
    party = [mon("BROOK", 200, 211), mon("REED", 40, 134)]
    bag = {"POTION": 1}
    d, used = heal_driver(monkeypatch, party, bag)
    assert d.heal_party() == {"BROOK": "POTION", "REED": "no item"}
    assert used == [(0, "POTION")]
    assert bag == {}


def test_heal_party_honors_the_items_whitelist(monkeypatch):
    party = [mon("BROOK", 200, 211)]
    bag = {"POTION": 1, "HYPERPOTION": 1}
    d, used = heal_driver(monkeypatch, party, bag)
    assert d.heal_party(items=["HYPER POTION"]) == {"BROOK": "HYPER POTION"}
    assert bag == {"POTION": 1}


def test_heal_party_reports_a_mon_it_could_not_finish(monkeypatch):
    """HP topped up but the status has no cure in the bag: say so instead
    of claiming the mon is fine."""
    party = [mon("BROOK", 200, 211)]
    bag = {"POTION": 1}
    d, used = heal_driver(monkeypatch, party, bag, status={0: PSN})
    assert d.heal_party() == {"BROOK": "POTION (still hurt)"}


def test_heal_party_skips_eggs(monkeypatch):
    party = [mon("EGG", 0, 0, egg=True), mon("BROOK", 200, 211)]
    bag = {"POTION": 1}
    d, used = heal_driver(monkeypatch, party, bag)
    assert d.heal_party() == {"BROOK": "POTION"}
    assert used == [(1, "POTION")]


# -- the heal table itself comes from the ROM, not from this file -------------

def test_heal_table_is_read_from_the_rom_tables():
    """_load_heal_table must agree with data/items/heal_hp.asm,
    data/items/heal_status.asm, the ItemEffects jumptable and
    data/items/attributes.asm -- no hand-maintained game data."""
    from crystalagent import paths
    from crystalagent.charmap import Charmap
    from crystalagent.names import Names
    from crystalagent.symfile import Symbols
    if not paths.ROM.exists() or not paths.SYM.exists():
        pytest.skip("built ROM/sym not found")
    sym = Symbols(paths.SYM)
    names = Names(paths.ROM, sym, Charmap(paths.CHARMAP), paths.MAP_CONSTANTS)
    table = trek._load_heal_table(paths.ROM, sym, names)
    for key, item in TABLE.items():
        assert table[key] == item, key
    # a non-curative item never lands in the table
    assert "MOONSTONE" not in table
