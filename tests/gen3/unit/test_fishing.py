"""Fishing, without an emulator.

Three things here are worth a test and the rest is not:

1. **What the decomp says.** The step constants, the ``data[]`` slot indices
   and the reel timeouts are parsed rather than transcribed, so a test that
   pins the parsed values catches a bad parse the moment the decomp moves --
   which is the whole reason they are parsed.
2. **Which rod, and every refusal.** ``fish()`` must never press anything when
   its preconditions are unmet, and must say why in a word the caller can
   branch on.
3. **When A is pressed.** ``Fishing8`` gives thirty frames to reel; the state
   before it, the dot game, treats A as "give up". So "A on exactly one
   ``tStep``" is the single most load-bearing fact in the module, and it is
   asserted from both sides: pressed on the reel state, never pressed on any
   other -- least of all the dot game.

The fakes stop at the emulator. Struct layouts, the enum values and the
constants still come from the real decompilation, because faking those would
be faking the thing under test.
"""

import pytest

from pokeagent import fishing as F

pytestmark = pytest.mark.unit


# ---- fakes ---------------------------------------------------------------


class FakeSym:
    """Just the symbol sizes the bag driver derives its strides from."""

    def __init__(self, sizes):
        self.sizes = sizes

    def size(self, name):
        return self.sizes.get(name, 0)


class FakeEmu:
    """Byte-addressed memory, recorded input, and a state machine that
    advances when frames are run -- which is how the real one behaves."""

    def __init__(self, sizes=None, machine=None):
        self.sym = FakeSym(sizes or {
            "gBagPocketScrollStates": 20,     # 5 pockets x 4 bytes
            "gBagPockets": 40,                # 5 pockets x 8 bytes
            "sCurrentStartMenuActions": 10,
        })
        self.addrs = {
            "gBagPocketScrollStates": 0x03005D10,
            "gBagPockets": 0x083C1690,
            "gPaletteFade": 0x02037AB8,
            "sCurrentBagPocket": 0x02038559,
            "sNumStartMenuActions": 0x0202E8FD,
            "sCurrentStartMenuActions": 0x0202E8FE,
            "gSaveBlock1": 0x02025734,
        }
        self.mem = {}
        self.presses = []
        self.frames = 0
        self.machine = machine

    # -- addressing
    def resolve(self, where):
        if isinstance(where, int):
            return where
        if isinstance(where, tuple):
            return self.resolve(where[0]) + where[1]
        return self.addrs[where]

    def poke(self, where, *values):
        addr = self.resolve(where)
        for i, v in enumerate(values):
            self.mem[addr + i] = v

    def read(self, where, n=1):
        addr = self.resolve(where)
        return bytes(self.mem.get(addr + i, 0) for i in range(n))

    def u8(self, where, i=0):
        return self.read(where, i + 1)[i]

    def u16(self, where):
        return int.from_bytes(self.read(where, 2), "little")

    def u32(self, where):
        return int.from_bytes(self.read(where, 4), "little")

    # -- running
    def tick(self, frames=1):
        self.frames += frames
        if self.machine is not None:
            self.machine.advance()

    def run_sequence(self, seq):
        self.presses.append(seq)
        self.frames += 4
        if self.machine is not None:
            self.machine.advance()


class ScriptedFishing:
    """A ``tStep`` script that advances one state per batch of frames run."""

    def __init__(self, fields, steps, battle_at=None):
        self.fields = fields
        self.steps = list(steps)
        self.battle_at = battle_at
        self.i = 0
        self.battled = False

    @property
    def step(self):
        return self.steps[self.i] if self.i < len(self.steps) else None

    def advance(self):
        if self.i < len(self.steps):
            self.i += 1

    def data(self):
        if self.step is None:
            return None
        d = [0] * 16
        d[self.fields["tStep"]] = self.step
        d[self.fields["tFishingRod"]] = 1        # Good Rod: a 33-frame window
        d[self.fields["tRoundsPlayed"]] = 1
        return d

    def in_battle(self):
        """Once the encounter starts it STAYS started -- the real
        `in_battle()` does not go false again when Task_Fishing is
        destroyed."""
        if self.battle_at is not None and self.step == self.battle_at:
            self.battled = True
        return self.battled


class FakeCell:
    def __init__(self, kind, behavior, collision=0, elevation=1):
        self.kind = kind
        self.behavior = behavior
        self.collision = collision
        self.elevation = elevation


class FakeNav:
    #: behaviour byte -> Cell, keyed by (x, y)
    def __init__(self, cells):
        self.cells = cells

    def cell(self, map_name, x, y):
        return self.cells.get((x, y))

    def _is_water(self, cell):
        return cell.kind == "water"


class FakeNames:
    ITEMS = {262: "OLD ROD", 263: "GOOD ROD", 264: "SUPER ROD",
             4: "POTION"}

    def item(self, item_id):
        return self.ITEMS[item_id]


class FakeState:
    def __init__(self, bag=None, machine=None):
        self._bag = bag if bag is not None else {"key_items": {}}
        self.machine = machine
        self.sb1 = {field: 0x1000 + 4 * i
                    for i, field in enumerate(F.POCKET_FIELDS.values())}

    def bag(self):
        return self._bag

    def tasks(self):
        return []

    def task_data(self, name):
        if name != F.FISHING_TASK or self.machine is None:
            return None
        return self.machine.data()


class FakeDriver:
    def __init__(self, consts, bag=None, cells=None, machine=None,
                 facing="U", pos=(10, 10), underwater=False, scene=False,
                 surfing=True, elevation=1):
        self.consts = consts
        self.names = FakeNames()
        self.machine = machine
        self.emu = FakeEmu(machine=machine)
        self.state = FakeState(bag=bag, machine=machine)
        self.nav = FakeNav(cells or {})
        self._facing = facing
        self._pos = pos
        self._underwater = underwater
        self._scene = scene
        self._surfing = surfing
        self._elevation = elevation
        self.flushed = 0
        self.settled = 0

    # the slice of Driver that Fishing actually uses
    def facing(self):
        return self._facing

    def pos(self):
        return self._pos

    def map_name(self):
        return "Route118"

    def _ahead(self, d):
        dx, dy = {"U": (0, -1), "D": (0, 1), "L": (-1, 0), "R": (1, 0)}[d]
        return (self._pos[0] + dx, self._pos[1] + dy)

    def underwater(self):
        return self._underwater

    def is_surfing(self):
        return self._surfing

    def elevation(self):
        return self._elevation

    def scene_active(self):
        return self._scene

    def in_battle(self):
        return bool(self.machine and self.machine.in_battle())

    def flush_dialog(self, *a, **k):
        self.flushed += 1
        return True

    def settle(self, *a, **k):
        self.settled += 1
        return True

    def advance_scene(self, *a, **k):
        return True


def make(consts, **kw):
    d = FakeDriver(consts, **kw)
    return d, F.Fishing(d)


WATER_AHEAD = {(10, 9): FakeCell("water", 0x10)}
LAND_AHEAD = {(10, 9): FakeCell("grass", 0x02)}
BOTH_RODS = {"key_items": {"GOOD ROD": 1, "SUPER ROD": 1}}
GOOD_ONLY = {"key_items": {"GOOD ROD": 1}}


# ---- what the decompilation says -----------------------------------------


def test_step_constants_come_out_of_the_decomp(consts):
    _d, f = make(consts)
    # src/field_player_avatar.c:1507-1512
    assert (f.START_ROUND, f.GOT_BITE, f.ON_HOOK) == (3, 6, 9)
    assert (f.NO_BITE, f.GOT_AWAY, f.SHOW_RESULT) == (11, 12, 13)
    # The reel window is the state AFTER "Oh! A Bite!", and the dot game is
    # the state after START_ROUND. Both are derived, and both matter.
    assert f.REEL == 7
    assert f.DOT_GAME == 4
    assert f.REEL != f.DOT_GAME


def test_task_data_slots_are_parsed_not_counted():
    fields = F.fishing_fields()
    # src/field_player_avatar.c:1498-1505
    assert fields["tStep"] == 0
    assert fields["tFrameCounter"] == 1
    assert fields["tRoundsPlayed"] == 12
    assert fields["tFishingRod"] == 15


def test_reel_timeouts_are_parsed():
    # Fishing8's `const s16 reelTimeouts[3] = {36, 33, 30}`, indexed by rod.
    assert F.reel_timeouts() == (36, 33, 30)


def test_menu_enums_are_parsed_from_the_source():
    assert F.enum_values("src/start_menu.c", "MENU_ACTION_BAG")[
        "MENU_ACTION_BAG"] == 2
    actions = F.enum_values("src/item_menu.c", "ITEM_ACTION_USE_0")
    assert actions["ITEM_ACTION_USE_0"] == 0
    assert actions["ITEM_ACTION_NONE"] == 8


# ---- which rod -----------------------------------------------------------


def test_prefers_the_best_rod_held(consts):
    _d, f = make(consts, bag=BOTH_RODS)
    assert [n for n, _ in f.held_rods()] == ["ITEM_SUPER_ROD", "ITEM_GOOD_ROD"]
    assert f.best_rod() == consts.items["ITEM_SUPER_ROD"]


def test_falls_back_down_the_preference_order(consts):
    _d, f = make(consts, bag={"key_items": {"GOOD ROD": 1, "OLD ROD": 1}})
    assert f.best_rod() == consts.items["ITEM_GOOD_ROD"]
    _d, f = make(consts, bag={"key_items": {"OLD ROD": 1}})
    assert f.best_rod() == consts.items["ITEM_OLD_ROD"]


def test_no_rod_held_is_no_rod(consts):
    _d, f = make(consts, bag={"key_items": {"HM01 CUT": 1}})
    assert f.best_rod() is None


def test_an_explicitly_named_rod_must_actually_be_held(consts):
    _d, f = make(consts, bag=GOOD_ONLY)
    assert f.resolve_rod("GOOD ROD") == consts.items["ITEM_GOOD_ROD"]
    assert f.resolve_rod("SUPER ROD") is None
    assert f.resolve_rod(consts.items["ITEM_SUPER_ROD"]) is None


# ---- the water in front --------------------------------------------------


def test_water_ahead_is_fishable(consts):
    _d, f = make(consts, cells=WATER_AHEAD)
    ok, why = f.faces_fishable_water()
    assert ok, why


def test_land_ahead_is_not(consts):
    _d, f = make(consts, cells=LAND_AHEAD)
    ok, why = f.faces_fishable_water()
    assert not ok
    assert "grass" in why


def test_a_waterfall_is_refused_like_canfish_does(consts):
    from pokeagent import nav as nav_mod

    cells = {(10, 9): FakeCell("water", nav_mod.WATERFALL)}
    _d, f = make(consts, cells=cells)
    ok, why = f.faces_fishable_water()
    assert not ok
    assert "waterfall" in why


def test_off_map_and_underwater_are_refused(consts):
    _d, f = make(consts, cells={})
    assert f.faces_fishable_water()[0] is False
    _d, f = make(consts, cells=WATER_AHEAD, underwater=True)
    ok, why = f.faces_fishable_water()
    assert not ok
    assert "underwater" in why


def test_water_with_collision_bits_is_refused(consts):
    """MEASURED live. Surfing on Route 119 at (28,47) facing WEST, (27,47) is
    MB_OCEAN_WATER (0x15) with collision 1 -- water you cannot swim into. The
    engine refused the cast with Dad's advice; only the collision bit told
    the two tiles apart, because both read as `water`.
    """
    cells = {(10, 9): FakeCell("water", 0x15, collision=1, elevation=0)}
    _d, f = make(consts, cells=cells)
    ok, why = f.faces_fishable_water()
    assert not ok
    assert "collision" in why
    # The very same behaviour with clear collision bits IS fishable: this is
    # the difference between (27,47) and (28,46) on that tile.
    cells = {(10, 9): FakeCell("water", 0x15, collision=0, elevation=1)}
    _d, f = make(consts, cells=cells)
    assert f.faces_fishable_water()[0] is True


def test_on_foot_the_player_must_be_at_elevation_three(consts):
    """IsPlayerFacingSurfableFishableWater checks PlayerGetZCoord() == 3."""
    _d, f = make(consts, cells=WATER_AHEAD, surfing=False, elevation=1)
    ok, why = f.faces_fishable_water()
    assert not ok
    assert "elevation" in why
    _d, f = make(consts, cells=WATER_AHEAD, surfing=False, elevation=3)
    assert f.faces_fishable_water()[0] is True
    # A surfer is at the water's own elevation and is not held to that rule.
    _d, f = make(consts, cells=WATER_AHEAD, surfing=True, elevation=1)
    assert f.faces_fishable_water()[0] is True


# ---- refusals ------------------------------------------------------------


def test_fish_refuses_with_no_rod_and_presses_nothing(consts):
    d, f = make(consts, bag={"key_items": {}}, cells=WATER_AHEAD)
    assert f.fish() is False
    assert f.last_reason == "no-rod"
    assert d.emu.presses == []


def test_fish_refuses_wrong_tile_and_presses_nothing(consts):
    d, f = make(consts, bag=GOOD_ONLY, cells=LAND_AHEAD)
    assert f.fish() is False
    assert f.last_reason == "wrong-tile"
    assert d.emu.presses == []


def test_fish_reports_cast_failed_when_the_bag_will_not_use_the_rod(consts):
    d, f = make(consts, bag=GOOD_ONLY, cells=WATER_AHEAD)

    def refuse(*a, **k):
        f.bag.last_reason = "the bag never opened"
        return False

    f.bag.use = refuse
    assert f.fish() is False
    assert f.last_reason == "cast-failed"
    assert "the bag never opened" in f.last_detail


def test_fish_reports_cast_failed_when_the_task_never_starts(consts):
    """The engine's own refusal: USE was pressed, CanFish said no."""
    d, f = make(consts, bag=GOOD_ONLY, cells=WATER_AHEAD)
    f.bag.use = lambda *a, **k: True
    assert f.fish() is False
    assert f.last_reason == "cast-failed"
    assert F.FISHING_TASK in f.last_detail
    # No A was pressed while waiting for the cast: the only presses a failed
    # cast may make are the ones that close the menu.
    assert not any(p.startswith("A") for p in d.emu.presses)


def _run_fish(consts, steps, battle_at=None, bag=GOOD_ONLY):
    fields = F.fishing_fields()
    machine = ScriptedFishing(fields, steps, battle_at=battle_at)
    d = FakeDriver(consts, bag=bag, cells=WATER_AHEAD, machine=machine)
    f = F.Fishing(d)
    f.bag.use = lambda *a, **k: True
    result = f.fish()
    return d, f, result


def test_got_away_is_reported(consts):
    d, f, ok = _run_fish(consts, [3, 4, 4, 5, 6, 7, 12, 13, 14, 15])
    assert ok is False
    assert f.last_reason == "got-away"
    assert d.flushed >= 1                 # the message box was cleared


def test_no_bite_is_reported(consts):
    d, f, ok = _run_fish(consts, [3, 4, 4, 5, 11, 13, 14, 15])
    assert ok is False
    assert f.last_reason == "no-bite"
    assert d.flushed >= 1


def test_an_encounter_is_the_only_true(consts):
    d, f, ok = _run_fish(
        consts, [3, 4, 4, 5, 6, 7, 8, 9, 10], battle_at=10)
    assert ok is True
    assert f.last_reason is None
    assert d.flushed == 0                 # a battle must not be flushed away


# ---- the reel window: A on exactly one state -----------------------------


def test_reel_presses_a_on_the_bite_state(consts):
    fields = F.fishing_fields()
    machine = ScriptedFishing(fields, [6, 7, 8, 9, 10], battle_at=10)
    d = FakeDriver(consts, bag=GOOD_ONLY, machine=machine)
    f = F.Fishing(d)
    assert f.reel() == "hooked"
    a_presses = [p for p in d.emu.presses if p.startswith("A")]
    assert len(a_presses) == 1
    assert f.last_steps == [6, 7, 8, 9, 10]


def test_reel_never_presses_a_during_the_dot_game(consts):
    """A in Fishing5 sets FISHING_NO_BITE / FISHING_GOT_AWAY on purpose."""
    fields = F.fishing_fields()
    steps = [0, 1, 2, 3, 4, 4, 4, 4, 5, 11, 13, 14, 15]
    machine = ScriptedFishing(fields, steps)
    d = FakeDriver(consts, bag=GOOD_ONLY, machine=machine)
    f = F.Fishing(d)
    assert f.reel() == "no-bite"
    assert d.emu.presses == []
    # Stops AT the terminal state; clearing the message is flush_dialog's job.
    assert f.last_steps == [0, 1, 2, 3, 4, 5, 11]


def test_a_missed_bite_is_read_off_the_states_that_ARE_observable(consts):
    """MEASURED live over twenty-odd casts. `Task_Fishing` runs every state
    that returns TRUE in the same frame, so FISHING_NO_BITE (11) and the two
    states after it are invisible: a missed bite polls as `[..., 5, 14, 15]`.
    Waiting to see 11 reported every failed cast as a timeout.
    """
    fields = F.fishing_fields()
    machine = ScriptedFishing(fields, [3, 4, 5, 14, 15])
    d = FakeDriver(consts, bag=GOOD_ONLY, machine=machine)
    assert F.Fishing(d).reel() == "no-bite"


def test_an_expired_reel_window_is_read_the_same_way(consts):
    """The only route to the result message THROUGH the reel state is the
    window expiring -- the dot game never sees an A press from us."""
    fields = F.fishing_fields()
    machine = ScriptedFishing(fields, [3, 4, 5, 6, 7, 14, 15])
    d = FakeDriver(consts, bag=GOOD_ONLY, machine=machine)
    f = F.Fishing(d)
    assert f.reel() == "got-away"
    assert len([p for p in d.emu.presses if p.startswith("A")]) == 1


def test_reel_does_not_wait_out_the_result_message(consts):
    """Fishing16 blocks on a text printer that pauses for a button, so the
    task never ends on its own; the loop must stop instead of burning its
    whole budget (which is what turned every failure into 'timeout')."""
    fields = F.fishing_fields()
    machine = ScriptedFishing(fields, [3, 4, 5, 14] + [15] * 4000)
    d = FakeDriver(consts, bag=GOOD_ONLY, machine=machine)
    f = F.Fishing(d)
    assert f.reel() == "no-bite"
    assert d.emu.frames < 200


def test_reel_plays_every_round_the_engine_asks_for(consts):
    """Fishing9 can send the machine back to FISHING_START_ROUND, and each
    round has its own bite -- so one A press per reel state, not one total."""
    fields = F.fishing_fields()
    steps = [3, 4, 5, 6, 7,      # round 1: bite, reel
             8, 3, 4, 5, 6, 7,   # Fishing9 sent us back; round 2
             8, 9, 10]
    machine = ScriptedFishing(fields, steps, battle_at=10)
    d = FakeDriver(consts, bag=GOOD_ONLY, machine=machine)
    f = F.Fishing(d)
    assert f.reel() == "hooked"
    assert len([p for p in d.emu.presses if p.startswith("A")]) == 2
    assert all(not p.startswith("A") for p in d.emu.presses
               if not p.startswith("A"))


def test_reel_stops_when_the_task_disappears(consts):
    fields = F.fishing_fields()
    machine = ScriptedFishing(fields, [3, 4])
    d = FakeDriver(consts, bag=GOOD_ONLY, machine=machine)
    f = F.Fishing(d)
    assert f.reel() == "vanished"
    assert d.emu.presses == []


# ---- clearing the result message ----------------------------------------


def test_clearing_the_hook_message_is_bounded_and_stops_at_the_task(consts):
    """Fishing11 pauses on its own text printer and needs ONE press. An
    unbounded press is what auto-fought seven hooked battles: `flush_dialog`
    kept pressing A into the encounter it had just started."""
    fields = F.fishing_fields()
    machine = ScriptedFishing(fields, [10, 10])
    d = FakeDriver(consts, bag=GOOD_ONLY, machine=machine)
    f = F.Fishing(d)
    presses = f._clear_result()
    assert presses == 2                       # the script's two states, then gone
    assert presses <= f.RESULT_PRESSES
    assert f.task() is None


def test_clearing_never_presses_into_the_dot_game_or_the_reel(consts):
    """Only the states ABOVE the reel window read no input; the two below it
    treat A as 'give up'."""
    fields = F.fishing_fields()
    for step in (F.Fishing(FakeDriver(consts)).DOT_GAME,
                 F.Fishing(FakeDriver(consts)).REEL):
        machine = ScriptedFishing(fields, [step] * 4)
        d = FakeDriver(consts, bag=GOOD_ONLY, machine=machine)
        assert F.Fishing(d)._clear_result() == 0
        assert d.emu.presses == []


def test_clearing_is_capped(consts):
    fields = F.fishing_fields()
    machine = ScriptedFishing(fields, [15] * 100)
    d = FakeDriver(consts, bag=GOOD_ONLY, machine=machine)
    f = F.Fishing(d)
    assert f._clear_result() == f.RESULT_PRESSES


# ---- the bag's scrolling list -------------------------------------------


def _bag(consts, pocket=4, cursor=0, top=0, slots=1):
    d = FakeDriver(consts)
    bag = F.Bag(d)
    base = d.emu.resolve("gBagPocketScrollStates") + pocket * bag.scroll_stride
    d.emu.poke(base, cursor, top, slots, min(slots, 7))
    return d, bag


def test_the_selected_row_is_scrolltop_plus_cursorpos(consts):
    """cursorPos is a SCREEN row, capped at seven. On a long pocket it stops
    moving while the list scrolls under it, so reading it alone names the
    wrong item -- which is exactly the bug this accounting prevents."""
    d, bag = _bag(consts, pocket=4, cursor=6, top=5, slots=20)
    assert bag.scroll_state(4)["cursorPos"] == 6
    assert bag.row(4) == 11


def test_drive_row_reaches_a_row_below_the_visible_window(consts):
    """Twelve items, seven rows on screen: row 11 is only reachable by
    scrolling, and the driver must keep pressing until the ENGINE's row
    number arrives -- not until it has pressed some computed number of times.
    """
    d, bag = _bag(consts, pocket=4, cursor=0, top=0, slots=12)
    base = d.emu.resolve("gBagPocketScrollStates") + 4 * bag.scroll_stride
    d.emu.poke(d.emu.resolve("sCurrentBagPocket"), 4)
    keys = []

    def tap(key):
        """sub_80A4F74's own rule: move the cursor until it hits cursorMax,
        then scroll (src/item_menu.c:1551-1592)."""
        keys.append(key)
        s = bag.scroll_state(4)
        cur, top, n, cmax = s["cursorPos"], s["scrollTop"], s["numSlots"], s["cursorMax"]
        if key == "DOWN" and top + cur < n - 1:
            if cur < cmax:
                cur += 1
            else:
                top += 1
        elif key == "UP":
            if cur:
                cur -= 1
            elif top:
                top -= 1
        d.emu.poke(base, cur, top, n, cmax)
        return True

    bag._tap = tap
    assert bag.drive_row(11) is True
    assert bag.row(4) == 11
    assert bag.scroll_state(4)["scrollTop"] > 0    # it really did scroll
    assert keys and set(keys) == {"DOWN"}


def test_drive_row_refuses_a_stuck_list(consts):
    d, bag = _bag(consts, pocket=4, cursor=0, top=0, slots=12)
    d.emu.poke(d.emu.resolve("sCurrentBagPocket"), 4)
    bag._tap = lambda key: True                    # a list that ignores input
    assert bag.drive_row(5) is False
    assert "stuck" in bag.last_reason


def test_the_live_start_menu_is_read_not_guessed(consts):
    d, bag = _bag(consts)
    # POKEDEX, POKEMON, BAG, PLAYER, SAVE, OPTION, EXIT -- BAG at row 2.
    d.emu.poke(d.emu.resolve("sNumStartMenuActions"), 7)
    d.emu.poke(d.emu.resolve("sCurrentStartMenuActions"), 0, 1, 2, 4, 5, 6, 7)
    assert bag.start_menu_rows() == [0, 1, 2, 4, 5, 6, 7]
    assert bag.start_menu_rows().index(bag.menu_action_bag) == 2
    # Before the POKEDEX exists the same menu is shorter and BAG moves up.
    d.emu.poke(d.emu.resolve("sNumStartMenuActions"), 6)
    d.emu.poke(d.emu.resolve("sCurrentStartMenuActions"), 1, 2, 4, 5, 6, 7)
    assert bag.start_menu_rows().index(bag.menu_action_bag) == 1


def test_open_refuses_while_a_script_owns_input(consts):
    d = FakeDriver(consts, scene=True)
    bag = F.Bag(d)
    assert bag.open() is False
    assert "script owns input" in bag.last_reason
    assert d.emu.presses == []
