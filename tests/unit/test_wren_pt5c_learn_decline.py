"""wren pt5c: DECLINE mid-battle learn flow (the GATOR/SCREECH wedge).

The mid-battle _AskForgetMoveText (pokecrystal data/text/common_3.asm)
scrolls '<MON> is / trying to learn / <MOVE>. / But <MON> / can't learn
more / than four moves. / Delete an older / move to make room / for
<MOVE>?' through a TWO-LINE box: the full '<MON> is trying to learn
<MOVE>.' sentence is never on screen at once, and the middle pages
carried no _LEARN_MARKERS. Pre-fix that skipped the policy consult
entirely AND dropped the per-flow state mid-flow, so a DECLINE answered
nothing, learn_moves=True auto-YESed the make-room prompt, and the
forget menu opened with the cursor parked on an HM (the caller retried
fight() ~150x). These tests drive the REAL _battle_text_handler /
_resolve_learn_flow over a page-accurate scripted fake."""
import logging

import pytest

from crystalagent.driver import Driver

pytestmark = pytest.mark.unit

MON = "GATOR"
NEW = "SCREECH"
MOVES = ["SURF", "BITE", "RAGE", "SLASH"]   # SURF: the HM from the live wedge


def render(w):
    rows = [" " * 20 for _ in range(18)]
    s = w["stage"]

    def box(*lines):
        for i, t in enumerate(lines):
            rows[13 + i] = t.ljust(20)[:20]

    if s == "p1a":
        box(f"{MON} is", "trying to learn")
    elif s == "p1b":
        box("trying to learn", f"{NEW}.")
    elif s == "scroll":                      # marker-less scroll transient
        box(f"{NEW}.", f"But {MON}")
    elif s == "p2a":
        box(f"But {MON}", "can't learn more")
    elif s == "p2b":
        box("can't learn more", "than four moves.")
    elif s == "p3a":
        box("Delete an older", "move to make room")
    elif s == "makeroom":
        box("move to make room", f"for {NEW}?", "YES  NO")
    elif s == "menu":
        rows[1] = "Which move should be"
        rows[2] = "forgotten?".ljust(20)
        for i, mv in enumerate(w["moves"]):
            glyph = "▶" if i == w["cursor"] else " "
            rows[4 + i] = f" {glyph}{mv}".ljust(20)[:20]
    elif s == "refuse":
        box("HM moves", "can't be forgotten")
    elif s == "stop":
        box("Stop learning", f"{NEW}?", "YES  NO")
    return rows                              # "done": all blank


class FakeEmu:
    def __init__(self, world):
        self.frame = 0
        self.world = world
        self.u8 = {}

    def tick(self, n=1):
        self.frame += n

    def screen_text(self):
        w = self.world
        if w["stage"] == "scroll" and self.frame >= w["settle_at"]:
            w["stage"] = "p2a"               # scroll animation finished
            w["history"].append("p2a")
        return render(w)

    def read_u8(self, sym):
        return self.u8.get(sym, 0)


def flow_driver(policy=None, stage="p1a", moves=MOVES):
    """Real Driver methods over the paginated mid-battle learn text.
    press() advances the world exactly like the game: A pages the text
    (with a marker-less scroll animation between page 1 and page 2 that
    settles only after ~60 frames), B at make-room answers NO, B on the
    forget menu backs out to 'Stop learning?'."""
    d = Driver.__new__(Driver)
    world = {"stage": stage, "cursor": 0, "moves": list(moves),
             "declined": False, "downs": 0, "confirm_rows": [],
             "history": [stage], "settle_at": 0}
    d.emu = FakeEmu(world)
    d.move_changes = []
    if policy is not None:
        d.learn_policy = policy              # else: class default (None)

    def goto(stage):
        world["stage"] = stage
        world["history"].append(stage)

    def press(seq):
        for tok in seq.split():
            d.emu.tick(25)
            btn, s = tok.split(":")[0], world["stage"]
            if btn == "A":
                if s == "p1a":
                    goto("p1b")
                elif s == "p1b":             # para: scroll animation next
                    world["settle_at"] = d.emu.frame + 60
                    goto("scroll")
                elif s == "p2a":
                    goto("p2b")
                elif s == "p2b":
                    goto("p3a")
                elif s == "p3a":
                    goto("makeroom")
                elif s == "makeroom":
                    goto("menu")             # YES: make room
                elif s == "menu":
                    picked = world["moves"][world["cursor"]]
                    world["confirm_rows"].append(picked)
                    if picked in Driver.HM_MOVES:
                        goto("refuse")
                    else:
                        world["moves"][world["cursor"]] = NEW
                        goto("done")
                elif s == "refuse":
                    goto("menu")             # menu reopens, cursor kept
                elif s == "stop":
                    world["declined"] = True
                    goto("done")             # YES: stop learning
            elif btn == "B":
                if s == "makeroom":
                    goto("stop")             # NO at make-room
                elif s == "menu":
                    goto("stop")             # back out of the forget menu
                elif s == "stop":
                    goto("makeroom")         # "don't stop": loops back
            elif btn == "D" and s == "menu":
                world["downs"] += 1
                world["cursor"] = (world["cursor"] + 1) % len(world["moves"])

    d.press = press
    d._party_moves = lambda: [(MON, list(world["moves"]))]
    return d, world


def policy_warnings(caplog):
    return [r.getMessage() for r in caplog.records
            if "learn_policy" in r.getMessage()]


# -- (a) DECLINE answers NO at make-room; forget menu never opens -------------

def test_decline_mid_battle_answers_no_never_opens_menu(caplog):
    calls = []

    def policy(mon, new_move, current_moves):
        calls.append((mon, new_move, list(current_moves)))
        return "DECLINE"

    d, world = flow_driver(policy)
    with caplog.at_level(logging.WARNING, logger="trek"):
        assert d._resolve_learn_flow() is True
    # consulted exactly once, assembled ACROSS the 2-line scroll pages
    assert calls == [(MON, NEW, MOVES)]
    assert world["declined"] is True             # 'Stop learning' confirmed
    assert "menu" not in world["history"]        # forget menu never reached
    assert world["confirm_rows"] == []
    assert world["moves"] == MOVES
    assert d.move_changes == []
    assert d._learn_flow is None
    assert policy_warnings(caplog) == []


# -- (b) safety net: forget menu + DECLINE -> B out + stop-learning confirm ---

def test_forget_menu_with_decline_backs_out_and_stops(caplog):
    d, world = flow_driver(stage="menu")         # wedge state: menu already up
    d._learn_flow = {"decision": "DECLINE", "consulted": True,
                     "answered": True, "mon": MON, "move": NEW, "misses": 0}
    with caplog.at_level(logging.WARNING, logger="trek"):
        assert d._resolve_learn_flow() is True
    assert world["confirm_rows"] == []           # nothing was forgotten
    assert world["downs"] == 0                   # menu never walked
    assert world["declined"] is True             # 'Stop learning' confirmed
    assert world["stage"] == "done"              # menu closed, flow over
    assert world["moves"] == MOVES
    assert d.move_changes == []


# -- (c) raising policy: exception text + args logged, source=auto-fallback ---

def test_raising_policy_logs_args_and_stamps_auto_fallback(caplog):
    def policy(mon, new_move, current_moves):
        raise ValueError("model asleep")

    d, world = flow_driver(policy)
    with caplog.at_level(logging.WARNING, logger="trek"):
        assert d._resolve_learn_flow() is True
    raised = [w for w in policy_warnings(caplog) if "raised" in w]
    assert len(raised) == 1
    assert "model asleep" in raised[0]           # the exception text
    assert repr(MON) in raised[0] and repr(NEW) in raised[0]
    assert repr(MOVES) in raised[0]              # the args it was called with
    # auto took over: skipped the HM under the cursor, sacrificed slot 2
    assert world["confirm_rows"] == ["BITE"]
    assert d.move_changes == [{"mon": MON, "forgot": "BITE",
                               "learned": NEW, "slot": 2,
                               "source": "auto-fallback"}]


# -- (d) policy pick survives the scroll pages; source=policy -----------------

def test_policy_pick_survives_scroll_and_stamps_policy_source(caplog):
    d, world = flow_driver(lambda *a: "RAGE")
    with caplog.at_level(logging.WARNING, logger="trek"):
        assert d._resolve_learn_flow() is True
    assert world["confirm_rows"] == ["RAGE"]
    assert world["downs"] == 2                   # walked SURF -> BITE -> RAGE
    assert world["moves"] == ["SURF", "BITE", NEW, "SLASH"]
    assert d.move_changes == [{"mon": MON, "forgot": "RAGE",
                               "learned": NEW, "slot": 3,
                               "source": "policy"}]
    assert policy_warnings(caplog) == []


# -- no policy engaged: source=auto -------------------------------------------

def test_auto_mid_battle_stamps_auto_source():
    d, world = flow_driver()                     # learn_policy class default
    assert d._resolve_learn_flow() is True
    assert d.move_changes == [{"mon": MON, "forgot": "BITE",
                               "learned": NEW, "slot": 2,
                               "source": "auto"}]


# -- per-flow state survives transient marker gaps, then expires --------------

def test_handler_state_survives_transient_marker_gaps():
    d, world = flow_driver(lambda *a: "DECLINE")
    blank = [" " * 20 for _ in range(18)]
    d._battle_text_handler(d.emu.screen_text())  # p1a: mon parsed
    d._battle_text_handler(d.emu.screen_text())  # p1b: policy consulted
    assert d._learn_flow["decision"] == "DECLINE"
    for _ in range(3):                           # scroll transients tolerated
        assert d._battle_text_handler(blank) is False
        assert d._learn_flow is not None
    assert d._learn_flow["decision"] == "DECLINE"
    assert d._battle_text_handler(blank) is False   # 4th miss: flow is over
    assert d._learn_flow is None
