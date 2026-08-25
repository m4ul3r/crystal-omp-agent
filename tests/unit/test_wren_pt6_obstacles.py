"""wren pt6 frictions (trek.py side): field-obstacle clearing.

Live evidence from the badge-8 leg: bumping a whirlpool/waterfall raises
wScriptMode==2 for ~60 frames with textbox()==False and the YES/NO
ask-menu drawn in BLANK glyphs (real but invisible); a pause->A->pause
cadence answers it -- a fuzzer found '.:40 A:8 .:30'-style sequences work
where tight A-mash loops always fail. These tests script exactly those
wScriptMode/textbox/pos sequences against a frame-driven fake emu.
"""
import pytest

from trek import Driver, _tile_kind

pytestmark = pytest.mark.unit


# -- _tile_kind: obstacle terrain words (observe()'s tiles{}) ----------------

def test_tile_kind_field_obstacles():
    assert _tile_kind(0x24) == "whirlpool"
    assert _tile_kind(0x33) == "waterfall"
    assert _tile_kind(0x27) == "buoy"          # COLL_BUOY
    assert _tile_kind(0xC0) == "buoy"          # water side-wall family
    assert _tile_kind(0xC7) == "buoy"


def test_tile_kind_sidewalls_name_blocked_entries():
    assert _tile_kind(0xB0) == "sidewall-l"    # COLL_RIGHT_WALL
    assert _tile_kind(0xB1) == "sidewall-r"    # COLL_LEFT_WALL
    assert _tile_kind(0xB2) == "sidewall-d"    # COLL_UP_WALL
    assert _tile_kind(0xB3) == "sidewall-u"    # COLL_DOWN_WALL
    assert _tile_kind(0xB4) == "sidewall-ul"
    assert _tile_kind(0xB5) == "sidewall-ur"
    assert _tile_kind(0xB6) == "sidewall-dl"
    assert _tile_kind(0xB7) == "sidewall-dr"


def test_tile_kind_existing_words_unchanged():
    assert _tile_kind(0x00) == "floor"
    assert _tile_kind(0x14) == "grass"
    assert _tile_kind(0x18) == "grass"
    assert _tile_kind(0x29) == "water"
    assert _tile_kind(0x71) == "warp"
    assert _tile_kind(0xA0) == "ledge-r"
    assert _tile_kind(0x23) == "ice"
    assert _tile_kind(0x60) == "pit"
    assert _tile_kind(0x07) == "blocked"       # plain wall stays generic


# -- frame-driven fake emulator ----------------------------------------------

class FakeEmu:
    """Ticks a scripted World one frame at a time. Buttons arrive as the
    pyboy names Driver.press produces ('a', 'right', ...)."""

    def __init__(self, world):
        self.frame = 0
        self.u8 = {"wMapGroup": 1, "wMapNumber": 2,
                   "wXCoord": 5, "wYCoord": 5}
        self.rows = [" " * 20 for _ in range(18)]
        self.tm = [0] * 360                    # tm[240]==0x79 <=> textbox
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
    """Base per-frame script: rising-edge detection, held-frame counting,
    and the fuzzer's cadence bookkeeping (gaps between A press starts)."""

    def __init__(self):
        self.emu = None
        self._prev = frozenset()
        self.held_r = 0
        self._last_a = None
        self.a_gaps = []           # frame gaps between successive A presses
        self.right_presses = 0     # rising edges of 'right'

    def step(self, buttons):
        rising = buttons - self._prev
        self._prev = buttons
        self.held_r = self.held_r + 1 if "right" in buttons else 0
        if "right" in rising:
            self.right_presses += 1
        gap = None
        if "a" in rising:
            if self._last_a is not None:
                gap = self.emu.frame - self._last_a
                self.a_gaps.append(gap)
            self._last_a = self.emu.frame
        self.on_frame(buttons, "a" in rising, gap)

    def on_frame(self, buttons, a_edge, a_gap):
        pass


def bare_driver(world):
    d = Driver.__new__(Driver)
    d.emu = FakeEmu(world)
    d.fight_calls = 0

    def fight(**kw):
        d.fight_calls += 1
        d.emu.u8["wBattleMode"] = 0
    d.fight = fight
    return d


# -- clear_obstacle: whirlpool (invisible ask, cadence-gated) -----------------

class WhirlpoolWorld(World):
    """$24 whirlpool right of the player. Bumping it raises wScriptMode==2
    for 60 frames, textbox stays False. An A press with a >=40-frame gap
    from the previous A answers YES; tighter presses are swallowed (the
    fuzzer's finding). Once cleared, held right walks (1 cell / 12f)."""

    def __init__(self, movable=True):
        super().__init__()
        self.movable = movable
        self.cleared = False
        self.swallowed = 0
        self._script_until = -1

    def on_frame(self, buttons, a_edge, a_gap):
        e = self.emu
        if e.u8.get("wScriptMode") and e.frame >= self._script_until:
            e.u8["wScriptMode"] = 0
        if not self.cleared and self.held_r == 8 \
                and not e.u8.get("wScriptMode"):
            e.u8["wScriptMode"] = 2            # invisible ask-menu
            self._script_until = e.frame + 60
        if a_edge and e.u8.get("wScriptMode") and not self.cleared:
            if a_gap is None or a_gap >= 40:
                self.cleared = True
                self._script_until = e.frame + 20   # text winds down
            else:
                self.swallowed += 1            # mashed A: menu eats it
        if self.cleared and self.movable and self.held_r == 12:
            e.u8["wXCoord"] += 1
            self.held_r = 0


def test_whirlpool_prompt_answered_then_move():
    w = WhirlpoolWorld()
    d = bare_driver(w)
    assert d.clear_obstacle("R") == "moved"
    assert w.cleared
    assert d.emu.u8["wXCoord"] > 5


def test_whirlpool_cadence_never_tighter_than_40f():
    w = WhirlpoolWorld()
    d = bare_driver(w)
    d.clear_obstacle("R")
    assert w.swallowed == 0
    assert all(g >= 40 for g in w.a_gaps), w.a_gaps


def test_prompt_answered_but_step_blocked_reports_cleared():
    """Info-prompt without a passable tile behind it (e.g. waterfall seen
    without the HM): the ask is answered but the step never takes."""
    w = WhirlpoolWorld(movable=False)
    d = bare_driver(w)
    assert d.clear_obstacle("R", tries=2) == "cleared-not-moved"
    assert w.cleared
    assert d.emu.u8["wXCoord"] == 5


# -- clear_obstacle: plain wall (no prompt, bounded) --------------------------

def test_plain_wall_fails_without_spinning_past_tries():
    w = World()                                # nothing ever reacts
    d = bare_driver(w)
    assert d.clear_obstacle("R", tries=3) == "failed"
    # exactly one bump + one verify move per attempt, no extra spinning
    assert w.right_presses == 2 * 3
    assert d.emu.u8["wXCoord"] == 5


# -- clear_obstacle: surf-mount (visible ask raised by an A poke) -------------

class SurfWorld(World):
    """Water right of the player. Bumping only turns to face it -- the
    'The water is calm... SURF?' ask needs an explicit A while facing,
    then raises wScriptMode==2 WITH a visible textbox. A properly spaced
    A answers YES; the mount animation slides onto the water ~10 frames
    later."""

    def __init__(self):
        super().__init__()
        self.faced = False
        self.ask = False
        self.answered = False
        self._mount_at = None

    def on_frame(self, buttons, a_edge, a_gap):
        e = self.emu
        if "right" in buttons:
            self.faced = True
        if a_edge:
            if self.ask and not self.answered:
                if a_gap is None or a_gap >= 40:
                    self.answered = True
                    self._mount_at = e.frame + 10
                    e.tm[240] = 0              # ask box closes
            elif self.faced and not self.answered:
                self.ask = True
                e.u8["wScriptMode"] = 2
                e.tm[240] = 0x79               # visible textbox
        if self._mount_at is not None and e.frame >= self._mount_at \
                and e.u8["wXCoord"] == 5:
            e.u8["wXCoord"] = 6                # slid onto the water cell
            e.u8["wScriptMode"] = 0


def test_surf_mount_prompt_same_cadence():
    w = SurfWorld()
    d = bare_driver(w)
    assert d.clear_obstacle("R") == "moved"
    assert w.answered
    assert d.emu.u8["wXCoord"] == 6
    assert all(g >= 40 for g in w.a_gaps), w.a_gaps


# -- move_settled: stable-position sampling -----------------------------------

class SlideWorld(World):
    """Step lands at held-8, then a slide carries one MORE cell 15 frames
    after the 40-frame hold releases: a single post-press pos sample
    reads the mid-slide cell (x=6), not where the player stops (x=7)."""

    def __init__(self):
        super().__init__()
        self._slide_at = None

    def on_frame(self, buttons, a_edge, a_gap):
        e = self.emu
        if self.held_r == 8 and e.u8["wXCoord"] == 5:
            e.u8["wXCoord"] = 6
            self._slide_at = e.frame + 47      # frame 55: after hold ends
        if self._slide_at is not None and e.frame >= self._slide_at \
                and e.u8["wXCoord"] == 6:
            e.u8["wXCoord"] = 7


def test_move_settled_waits_out_the_slide():
    w = SlideWorld()
    d = bare_driver(w)
    assert d.move_settled("R") == "moved"
    # returned only after the position settled, not on the mid-slide read
    assert d.emu.u8["wXCoord"] == 7
    assert d.emu.frame < 600                   # and without burning max_frames


def test_move_settled_blocked_wall():
    d = bare_driver(World())
    assert d.move_settled("R") == "blocked"
    assert d.emu.u8["wXCoord"] == 5


class BattleWorld(World):
    """The step lands (x=6 at held-8), then a wild battle interrupts at
    frame 12. As of wren pt6 move_settled SURFACES that battle instead
    of playing it out: a step is not a journey, so the disposition
    (ko/catch/flee) belongs to the caller unless fight=True."""

    def on_frame(self, buttons, a_edge, a_gap):
        e = self.emu
        if self.held_r == 8 and e.u8["wXCoord"] == 5:
            e.u8["wXCoord"] = 6
        if e.frame == 12:
            e.u8["wBattleMode"] = 1


def test_move_settled_surfaces_battle_en_route():
    w = BattleWorld()
    d = bare_driver(w)
    assert d.move_settled("R") == "battle"
    assert d.fight_calls == 0                  # harness decided nothing
    assert d.emu.u8["wBattleMode"] == 1        # still up, for the caller


def test_move_settled_fights_only_when_asked():
    w = BattleWorld()
    d = bare_driver(w)
    assert d.move_settled("R", fight=True) == "moved"
    assert d.fight_calls == 1
    assert d.emu.u8["wBattleMode"] == 0


class TextboxWorld(World):
    """The step lands, then a textbox pops (sign/NPC chatter) at frame 20;
    move_settled must page it with A and still settle."""

    def on_frame(self, buttons, a_edge, a_gap):
        e = self.emu
        if self.held_r == 8 and e.u8["wXCoord"] == 5:
            e.u8["wXCoord"] = 6
        if e.frame == 20:
            e.tm[240] = 0x79
        if a_edge and e.tm[240] == 0x79:
            e.tm[240] = 0


def test_move_settled_pages_textbox_en_route():
    w = TextboxWorld()
    d = bare_driver(w)
    assert d.move_settled("R") == "moved"
    assert d.emu.tm[240] == 0                  # box was paged away
    assert d.emu.u8["wXCoord"] == 6


class WarpSlideWorld(World):
    """Held step crosses a connection: map number flips mid-settle."""

    def on_frame(self, buttons, a_edge, a_gap):
        e = self.emu
        if self.held_r == 8 and e.u8["wMapNumber"] == 2:
            e.u8["wMapNumber"] = 3
            e.u8["wXCoord"] = 0


def test_move_settled_reports_warp():
    d = bare_driver(WarpSlideWorld())
    assert d.move_settled("R") == "warp"
