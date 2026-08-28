"""Movement primitives must never decide a battle (session claude-wren pt6).

Live evidence this pins down:

  * A model-written "pacing" loop reported fights=0 while move_settled
    silently played out ~20 encounters with the DEFAULT policy, handed
    every exp share to the wrong mon, and whited the party out. The
    model was never asked ko / catch / flee for ~78 of ~80 wilds.
  * An unclamped random walk drifted onto a staircase and stranded the
    run three floors deep in Victory Road -- hence pace(box=...).

So: a step is not a journey. move_settled/pace SURFACE the battle
('battle') and leave the disposition to the caller. The journey helpers
(walk/goto/travel) may still clear encounters, but only through the one
_on_battle() path so a policy/encounter hook always applies.

Fakes only -- no emulator boots.
"""
import pytest

import trek
from trek import Driver

pytestmark = pytest.mark.unit


# -- frame-driven fake emulator ----------------------------------------------

class FakeEmu:
    """Ticks a scripted World one frame at a time. Buttons arrive as the
    pyboy names Driver.press produces ('a', 'right', ...)."""

    def __init__(self, world, x=5, y=5):
        self.frame = 0
        self.u8 = {"wMapGroup": 1, "wMapNumber": 2,
                   "wXCoord": x, "wYCoord": y}
        self.rows = [" " * 20 for _ in range(18)]
        self.tm = [0] * 360                    # tm[240] == 0x79 <=> textbox
        self.world = world
        world.emu = self

    def tilemap(self):
        return self.tm

    def screen_text(self):
        return list(self.rows)

    def read_u8(self, sym):
        return self.u8.get(sym, 0)

    def tick(self, n=1):
        self._advance(frozenset(), n)

    def run_sequence(self, steps):
        for buttons, frames in steps:
            self._advance(frozenset(buttons), frames)

    def _advance(self, buttons, frames):
        for _ in range(frames):
            self.frame += 1
            self.world.step(buttons)


class World:
    """Per-frame script base: rising-edge detection and held counters for
    each of the four direction buttons."""

    BUTTON = {"U": "up", "D": "down", "L": "left", "R": "right"}

    def __init__(self):
        self.emu = None
        self._prev = frozenset()
        self.held = {mv: 0 for mv in "UDLR"}

    def step(self, buttons):
        self._prev = buttons
        for mv, btn in self.BUTTON.items():
            self.held[mv] = self.held[mv] + 1 if btn in buttons else 0
        self.on_frame(buttons)

    def on_frame(self, buttons):
        pass


class FakeNames:
    maps = {}                                  # map_name() -> '?1:2'


def _skeleton(world, x=5, y=5):
    """Driver skeleton with the __init__-only attrs the shared battle path
    reads (whiteout bookkeeping, names) filled in."""
    d = Driver.__new__(Driver)
    d.emu = FakeEmu(world, x=x, y=y)
    d.names = FakeNames()
    d._whiteout_pending = False
    d.whiteouts = 0
    d.whiteout_policy = "abort"
    d.fight_calls = 0
    return d


def bare_driver(world, **attrs):
    """Driver skeleton over a scripted world, with fight() counted."""
    d = _skeleton(world, x=attrs.pop("x", 5), y=attrs.pop("y", 5))

    def fight(**kw):
        d.fight_calls += 1
        d.emu.u8["wBattleMode"] = 0
    d.fight = fight
    for k, v in attrs.items():
        setattr(d, k, v)
    return d


# -- move_settled: the battle is SURFACED, not swallowed ---------------------

class StepThenBattleWorld(World):
    """The held step lands at held-8, then a wild jumps us at frame 12."""

    def on_frame(self, buttons):
        e = self.emu
        if self.held["R"] == 8 and e.u8["wXCoord"] == 5:
            e.u8["wXCoord"] = 6
        if e.frame == 12:
            e.u8["wBattleMode"] = 1


def test_move_settled_default_surfaces_battle_and_never_fights():
    d = bare_driver(StepThenBattleWorld())
    assert d.move_settled("R") == "battle"
    assert d.fight_calls == 0
    assert d.emu.u8["wBattleMode"] == 1     # left up for the caller to decide


def test_move_settled_default_surfaces_even_with_auto_fight_on():
    # auto_fight stays True (goto/travel need it) -- it must NOT drag the
    # step primitives back into deciding battles.
    d = bare_driver(StepThenBattleWorld(), auto_fight=True)
    assert d.move_settled("R") == "battle"
    assert d.fight_calls == 0


def test_move_settled_fight_true_calls_fight_exactly_once():
    d = bare_driver(StepThenBattleWorld())
    assert d.move_settled("R", fight=True) == "moved"
    assert d.fight_calls == 1
    assert d.emu.u8["wBattleMode"] == 0


def test_move_settled_fight_false_is_explicit_refusal():
    d = bare_driver(StepThenBattleWorld(),
                    auto_fight=True, auto_fight_steps=True)
    assert d.move_settled("R", fight=False) == "battle"
    assert d.fight_calls == 0


def test_move_settled_auto_fight_steps_opt_in_restores_old_behaviour():
    d = bare_driver(StepThenBattleWorld(), auto_fight_steps=True)
    assert d.move_settled("R") == "moved"
    assert d.fight_calls == 1


def test_move_settled_manual_auto_fight_beats_the_step_opt_in():
    # auto_fight=False means "the decider owns battles"; the step opt-in
    # must not be able to override that.
    d = bare_driver(StepThenBattleWorld(),
                    auto_fight=False, auto_fight_steps=True)
    assert d.move_settled("R") == "battle"
    assert d.fight_calls == 0


class NoBattleWorld(World):
    """Plain step, no encounter -- the un-battled path must be untouched."""

    def on_frame(self, buttons):
        e = self.emu
        if self.held["R"] == 8 and e.u8["wXCoord"] == 5:
            e.u8["wXCoord"] = 6


def test_move_settled_plain_step_unchanged():
    d = bare_driver(NoBattleWorld())
    assert d.move_settled("R") == "moved"
    assert d.emu.u8["wXCoord"] == 6
    assert d.fight_calls == 0


# -- a default-mode pacing loop performs ZERO fight() calls -----------------

class GridWorld(World):
    """Walkable box with a battle cell: a held direction moves one cell at
    held-8 if the target is inside the walls; stepping onto `battle_cell`
    raises wBattleMode. This is the shape of the grass belt the live
    pacing loop ran on."""

    def __init__(self, walls=(1, 8, 1, 8), battle_cell=(6, 5)):
        super().__init__()
        self.x_lo, self.x_hi, self.y_lo, self.y_hi = walls
        self.battle_cell = battle_cell
        self.visited = []
        self.STEP = {"U": (0, -1), "D": (0, 1), "L": (-1, 0), "R": (1, 0)}

    def on_frame(self, buttons):
        e = self.emu
        if e.u8.get("wBattleMode"):
            return
        for mv, (dx, dy) in self.STEP.items():
            if self.held[mv] != 8:
                continue
            nx = e.u8["wXCoord"] + dx
            ny = e.u8["wYCoord"] + dy
            if not (self.x_lo <= nx <= self.x_hi
                    and self.y_lo <= ny <= self.y_hi):
                return                       # wall: 'blocked'
            e.u8["wXCoord"], e.u8["wYCoord"] = nx, ny
            self.visited.append((nx, ny))
            if (nx, ny) == self.battle_cell:
                e.u8["wBattleMode"] = 1
            return


def test_default_pacing_loop_over_a_battle_cell_never_fights():
    """The headline regression: the loop the model hand-rolls must not
    let the harness fight a single battle on its behalf."""
    d = bare_driver(GridWorld(battle_cell=(6, 5)), auto_fight=True)
    total_battles = 0
    for _ in range(6):
        r = d.pace(20, box=(4, 7, 4, 7))
        total_battles += r["battles"]
        if r["stopped"] == "battle":
            # the caller decides -- here: walk away without fighting
            d.emu.u8["wBattleMode"] = 0
    assert d.fight_calls == 0, "harness fought a battle nobody asked for"
    assert total_battles > 0, "world never produced an encounter"


# -- pace(): box clamping and stop reasons ----------------------------------

def pace_driver(**attrs):
    """Driver skeleton whose move_settled is a scripted grid stepper, so
    pace()'s own logic (clamping, stop reasons, battle routing) is what
    is under test."""
    d = _skeleton(World(), x=attrs.pop("x", 5), y=attrs.pop("y", 5))
    d.moves = []
    script = attrs.pop("script", {})   # step index (1-based) -> result
    walls = attrs.pop("walls", None)   # (x_lo, x_hi, y_lo, y_hi) walkable

    def fight(**kw):
        d.fight_calls += 1
        d.emu.u8["wBattleMode"] = 0
    d.fight = fight

    STEP = {"U": (0, -1), "D": (0, 1), "L": (-1, 0), "R": (1, 0)}

    def move_settled(mv, hold=40, max_frames=600, fight=None):
        d.moves.append(mv)
        assert fight is False, "pace must never let the primitive fight"
        forced = script.get(len(d.moves))
        if forced == "battle":
            d.emu.u8["wBattleMode"] = 1
            return "battle"
        if forced:
            return forced
        dx, dy = STEP[mv]
        nx = d.emu.u8["wXCoord"] + dx
        ny = d.emu.u8["wYCoord"] + dy
        if walls and not (walls[0] <= nx <= walls[1]
                          and walls[2] <= ny <= walls[3]):
            return "blocked"
        d.emu.u8["wXCoord"], d.emu.u8["wYCoord"] = nx, ny
        return "moved"
    d.move_settled = move_settled
    for k, v in attrs.items():
        setattr(d, k, v)
    return d


def test_pace_never_leaves_its_box():
    box = (4, 6, 4, 6)
    d = pace_driver(x=5, y=5)
    seen = []
    real = d.move_settled

    def watched(mv, **kw):
        r = real(mv, **kw)
        seen.append((d.emu.u8["wXCoord"], d.emu.u8["wYCoord"]))
        return r
    d.move_settled = watched
    r = d.pace(200, box=box)
    assert r["steps"] == 200 and r["stopped"] == "steps"
    assert seen, "pace took no steps"
    for x, y in seen:
        assert box[0] <= x <= box[1] and box[2] <= y <= box[3], (x, y)


def test_pace_box_of_one_cell_is_boxed_in_not_a_drift():
    d = pace_driver(x=5, y=5)
    r = d.pace(10, box=(5, 5, 5, 5))
    assert {k: r[k] for k in ("steps", "battles", "stopped")} == {"steps": 0, "battles": 0, "stopped": "boxed-in"}
    assert d.moves == []


def test_pace_corridor_dirs_only_uses_those_directions():
    d = pace_driver(x=5, y=5)
    d.pace(30, dirs="LR", box=(1, 9, 5, 5))
    assert set(d.moves) <= {"L", "R"}
    assert d.emu.u8["wYCoord"] == 5


def test_pace_walks_back_into_a_box_it_starts_outside_of():
    d = pace_driver(x=0, y=0)
    r = d.pace(8, box=(3, 4, 3, 4))
    assert r["stopped"] == "steps"
    x, y = d.emu.u8["wXCoord"], d.emu.u8["wYCoord"]
    assert 3 <= x <= 4 and 3 <= y <= 4, (x, y)


# -- pace(): battles -------------------------------------------------------

def test_pace_returns_on_the_first_battle():
    d = pace_driver(script={3: "battle"}, auto_fight=True)
    r = d.pace(20, box=(1, 9, 1, 9))
    assert {k: r[k] for k in ("steps", "battles", "stopped")} == {"steps": 2, "battles": 1, "stopped": "battle"}
    assert d.fight_calls == 0
    assert d.emu.u8["wBattleMode"] == 1     # still up: the model decides
    assert len(d.moves) == 3                # stopped dead, did not walk on


def test_pace_on_battle_fight_keeps_stepping():
    d = pace_driver(script={2: "battle", 5: "battle"}, auto_fight=True)
    r = d.pace(6, box=(1, 9, 1, 9), on_battle="fight")
    assert {k: r[k] for k in ("steps", "battles", "stopped")} == {"steps": 6, "battles": 2, "stopped": "steps"}
    assert d.fight_calls == 2                # routed through _on_battle
    assert len(d.moves) == 8                 # 6 steps + 2 battle turns


def test_pace_fight_mode_routes_through_the_one_battle_path():
    # on_battle='fight' must reach the policy via fight(), never a
    # private shortcut, and never via the primitive.
    d = pace_driver(script={1: "battle"}, auto_fight=True)
    d.pace(3, box=(1, 9, 1, 9), on_battle="fight")
    assert d.fight_calls == 1


def test_pace_fight_mode_declines_when_auto_fight_is_manual():
    d = pace_driver(script={2: "battle"}, auto_fight=False)
    r = d.pace(9, box=(1, 9, 1, 9), on_battle="fight")
    assert {k: r[k] for k in ("steps", "battles", "stopped")} == {"steps": 1, "battles": 1, "stopped": "declined"}
    assert d.fight_calls == 0
    assert "auto_fight=manual" in d.last_goto_reason


def test_pace_fight_mode_stops_on_a_whiteout():
    d = pace_driver(script={2: "battle"}, auto_fight=True)
    d._whiteout_pending = True
    d.whiteouts = 0
    d.whiteout_policy = "abort"
    r = d.pace(9, box=(1, 9, 1, 9), on_battle="fight")
    assert r["stopped"] == "whiteout"
    assert r["steps"] == 1 and r["battles"] == 1


# -- pace(): loud about nonsense, bounded on walls -------------------------

def test_pace_gives_up_on_walls_instead_of_spinning():
    d = pace_driver(x=5, y=5, walls=(5, 5, 5, 5))   # boxed by terrain
    r = d.pace(50, box=(1, 9, 1, 9))
    assert r["stopped"] == "blocked"
    assert r["steps"] == 0
    assert len(d.moves) == 8                        # bounded, not 50


def test_pace_zero_steps_is_a_no_op():
    d = pace_driver()
    r = d.pace(0)
    assert {k: r[k] for k in ("steps", "battles", "stopped")} == {
        "steps": 0, "battles": 0, "stopped": "steps"}
    assert d.moves == []


def test_pace_rejects_a_bad_on_battle():
    with pytest.raises(ValueError, match="on_battle"):
        pace_driver().pace(5, on_battle="catch")


def test_pace_rejects_an_inverted_box():
    with pytest.raises(ValueError, match="lo <= hi"):
        pace_driver().pace(5, box=(9, 3, 1, 4))


def test_pace_rejects_dirs_naming_nothing():
    with pytest.raises(ValueError, match="no direction"):
        pace_driver().pace(5, dirs="xyz")


# -- the shared journey path -----------------------------------------------

def test_on_battle_fights_when_auto_fight_is_on():
    d = bare_driver(World(), auto_fight=True)
    assert d._on_battle("goto") is True
    assert d.fight_calls == 1


def test_on_battle_hands_back_and_explains_when_manual():
    d = bare_driver(World(), auto_fight=False)
    assert d._on_battle("goto ROUTE_30 (5,5)") is False
    assert d.fight_calls == 0
    assert "auto_fight=manual" in d.last_goto_reason
    assert "goto ROUTE_30 (5,5)" in d.last_goto_reason


def test_on_battle_explicit_fight_arg_wins_over_manual():
    d = bare_driver(World(), auto_fight=False)
    assert d._on_battle("move_settled R", fight=True) is True
    assert d.fight_calls == 1


def test_decision_default_attributes_exist_on_bare_drivers():
    # class defaults, so a live kernel holding an older Driver instance
    # still reads them (same trick as last_goto_reason).
    d = Driver.__new__(Driver)
    assert d.auto_fight_steps is False
    assert d.encounter_policy is None
    assert d.decide_all is False


def test_pace_names_the_cell_and_directions_when_boxed_in():
    """A caller that re-calls pace() on a cell with no legal move spins
    forever (live: seven real minutes of '0/30 steps, stopped=blocked' in
    a Route 34 pocket). The result must carry enough to act on."""
    d = pace_driver(walls=(5, 5, 5, 5))   # a one-cell pocket: no move lands
    r = d.pace(10)
    assert r["stopped"] == "blocked" and r["steps"] == 0
    assert r["pos"] == (5, 5)
    assert set(r["blocked_dirs"]) == set("UDLR")


def test_goto_stops_when_a_naming_keyboard_eats_the_steps():
    """An egg hatching mid-walk opens the naming keyboard, which eats every
    step exactly like a stray menu -- but it must never be blind-pressed
    (gotcha 18). Live cost: TOGEPI hatched on ROUTE_34 and the run spent
    seven minutes storming 'unexplained blocked step' replans."""
    d = pace_driver(walls=(5, 5, 5, 5))
    d.keyboard_open = lambda: True
    d.nav = trek.TrekNav(trek.paths.REPO_ROOT)
    d.nav.blocked = {}
    d._refresh_nav_blocks = lambda: None
    d.map_name = lambda: "ROUTE_34"
    d._map_const = lambda: "ROUTE_34"
    d.pos = lambda: (0, 0, 8, 10)
    d._step = lambda mv, **kw: "blocked"
    d.settle = lambda *a, **kw: None
    assert d.goto(8, 12) is False
    assert "naming-keyboard" in d.last_goto_reason
    assert "dismiss_keyboard" in d.last_goto_reason
