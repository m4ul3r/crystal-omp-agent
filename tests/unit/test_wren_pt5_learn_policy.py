"""wren pt5: model-controllable move learns (Driver.learn_policy).

The auto learn flow silently traded GATOR's BITE (irreplaceable dark
coverage) for SCARY FACE. These tests drive the REAL _battle_text_handler
against a scripted screen/world fake through _resolve_learn_flow and
check every policy outcome: targeted forget, DECLINE, None (auto,
byte-identical), stale request, raising policy, HM request."""
import logging

import pytest

from trek import Driver

pytestmark = pytest.mark.unit

MON = "GATOR"
NEW = "SCARY FACE"


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


def flow_driver(moves, policy=None, new_move=NEW):
    """Real Driver methods (_battle_text_handler, _consult_learn_policy,
    _resolve_learn_flow, _diff_learned_moves) over a scripted GSC learn
    flow: prompt -> make-room YES/NO -> forget menu (cursor walk, HM
    refusal) -> stop-learning YES/NO. press() advances the world exactly
    like the game does; the screen re-renders after every token."""
    d = Driver.__new__(Driver)
    d.emu = FakeEmu()
    d.move_changes = []
    if policy is not None:
        d.learn_policy = policy       # else: class default (None)
    world = {"stage": "prompt", "cursor": 0, "moves": list(moves),
             "declined": False, "downs": 0, "confirm_rows": []}

    def render():
        rows = [" " * 20 for _ in range(18)]
        s = world["stage"]
        if s == "prompt":
            # marker rows are matched per-row ("".join pads with spaces):
            # keep "trying to learn" contiguous, like pt4's fake does
            rows[12] = f"{MON} is".ljust(20)[:20]
            rows[13] = "trying to learn".ljust(20)[:20]
            rows[14] = f"{new_move}!".ljust(20)[:20]
        elif s == "makeroom":
            rows[12] = "make room for".ljust(20)[:20]
            rows[13] = f"{new_move}?".ljust(20)[:20]
            rows[14] = "YES  NO".ljust(20)[:20]
        elif s == "menu":
            rows[1] = "Which move should be".ljust(20)[:20]
            rows[2] = "forgotten?".ljust(20)[:20]
            for i, mv in enumerate(world["moves"]):
                glyph = "▶" if i == world["cursor"] else " "
                rows[4 + i] = f" {glyph}{mv}".ljust(20)[:20]
        elif s == "refuse":
            rows[12] = "HM moves".ljust(20)[:20]
            rows[13] = "can't be forgotten!".ljust(20)[:20]
        elif s == "stop":
            rows[12] = "Stop learning".ljust(20)[:20]
            rows[13] = f"{new_move}?".ljust(20)[:20]
            rows[14] = "YES  NO".ljust(20)[:20]
        d.emu.rows = rows

    def press(seq):
        for tok in seq.split():
            d.emu.tick(25)
            btn, s = tok.split(":")[0], world["stage"]
            if btn == "A":
                if s == "prompt":
                    world["stage"] = "makeroom"
                elif s == "makeroom":
                    world["stage"] = "menu"
                elif s == "menu":
                    picked = world["moves"][world["cursor"]]
                    world["confirm_rows"].append(picked)
                    if picked in Driver.HM_MOVES:
                        world["stage"] = "refuse"
                    else:
                        world["moves"][world["cursor"]] = new_move
                        world["stage"] = "done"
                elif s == "refuse":
                    world["stage"] = "menu"   # menu reopens, cursor kept
                elif s == "stop":
                    world["declined"] = True
                    world["stage"] = "done"
            elif btn == "B":
                if s == "makeroom":
                    world["stage"] = "stop"
                elif s == "stop":
                    world["stage"] = "makeroom"
            elif btn == "D" and s == "menu":
                world["downs"] += 1
                world["cursor"] = (world["cursor"] + 1) % len(world["moves"])
            render()

    d.press = press
    d._party_moves = lambda: [(MON, list(world["moves"]))]
    render()
    return d, world


MOVES = ["BITE", "WATER GUN", "RAGE", "SCRATCH"]


def learn_lines(caplog):
    return [r.getMessage() for r in caplog.records
            if r.getMessage().startswith("LEARN:")]


def policy_warnings(caplog):
    return [r.getMessage() for r in caplog.records
            if "learn_policy" in r.getMessage()]


# -- (a) policy names a mid-list move: THAT move is forgotten ----------------

def test_policy_forgets_requested_mid_list_move(caplog):
    calls = []

    def policy(mon, new_move, current_moves):
        calls.append((mon, new_move, list(current_moves)))
        return "RAGE"

    d, world = flow_driver(MOVES, policy)
    with caplog.at_level(logging.WARNING, logger="trek"):
        assert d._resolve_learn_flow() is True
    # consulted once, at the prompt, with the live moveset
    assert calls == [(MON, NEW, MOVES)]
    # the cursor was walked to RAGE (slot 3) and ONLY RAGE was confirmed
    assert world["downs"] == 2
    assert world["confirm_rows"] == ["RAGE"]
    assert world["moves"] == ["BITE", "WATER GUN", NEW, "SCRATCH"]
    assert learn_lines(caplog) == [
        f"LEARN: {MON} forgot RAGE -> learned {NEW} (slot 3)"]
    assert d.move_changes == [{"mon": MON, "forgot": "RAGE",
                               "learned": NEW, "slot": 3,
                               "source": "policy"}]
    assert policy_warnings(caplog) == []


# -- (b) policy DECLINEs: NO answered, nothing changes ------------------------

def test_policy_decline_answers_no_and_keeps_moves(caplog):
    d, world = flow_driver(MOVES, lambda *a: "DECLINE")
    with caplog.at_level(logging.WARNING, logger="trek"):
        assert d._resolve_learn_flow() is True
    assert world["declined"] is True
    assert world["confirm_rows"] == []          # forget menu never confirmed
    assert world["moves"] == MOVES
    assert d.move_changes == []
    assert learn_lines(caplog) == []


# -- (c) policy None / unset: auto behavior byte-identical --------------------

def test_policy_none_falls_back_to_auto_byte_identical(caplog):
    with caplog.at_level(logging.WARNING, logger="trek"):
        d_pol, w_pol = flow_driver(MOVES, lambda *a: None)
        assert d_pol._resolve_learn_flow() is True
        d_auto, w_auto = flow_driver(MOVES)     # learn_policy class default
        assert d_auto._resolve_learn_flow() is True
    # none of MOVES is in FORGET_PRIORITY: auto confirms slot 1 (BITE)
    assert w_pol["moves"] == w_auto["moves"] == [NEW, "WATER GUN",
                                                 "RAGE", "SCRATCH"]
    assert d_pol.move_changes == d_auto.move_changes == [
        {"mon": MON, "forgot": "BITE", "learned": NEW, "slot": 1,
         "source": "auto"}]
    assert policy_warnings(caplog) == []


def test_auto_still_walks_to_forget_priority_move():
    """Auto target-walking unchanged: LEER (FORGET_PRIORITY) is picked
    over the slot-1 move even when the policy stays out of the way."""
    moves = ["BITE", "WATER GUN", "LEER", "SCRATCH"]
    d, world = flow_driver(moves, lambda *a: None)
    assert d._resolve_learn_flow() is True
    assert world["confirm_rows"] == ["LEER"]
    assert world["moves"] == ["BITE", "WATER GUN", NEW, "SCRATCH"]


# -- (d) policy names a move not on the menu: one warning + auto --------------

def test_policy_stale_move_warns_once_and_falls_back(caplog):
    d, world = flow_driver(MOVES, lambda *a: "TACKLE")
    with caplog.at_level(logging.WARNING, logger="trek"):
        assert d._resolve_learn_flow() is True
    stale = [w for w in policy_warnings(caplog)
             if "not on the forget menu" in w]
    assert len(stale) == 1 and "TACKLE" in stale[0]
    # auto took over: slot 1 sacrificed, change recorded normally
    assert world["moves"] == [NEW, "WATER GUN", "RAGE", "SCRATCH"]
    assert d.move_changes == [{"mon": MON, "forgot": "BITE",
                               "learned": NEW, "slot": 1,
                               "source": "auto-fallback"}]


# -- (e) policy raises: one warning + auto ------------------------------------

def test_policy_exception_warns_once_and_falls_back(caplog):
    def policy(mon, new_move, current_moves):
        raise ValueError("model asleep")

    d, world = flow_driver(MOVES, policy)
    with caplog.at_level(logging.WARNING, logger="trek"):
        assert d._resolve_learn_flow() is True
    raised = [w for w in policy_warnings(caplog) if "raised" in w]
    assert len(raised) == 1 and "model asleep" in raised[0]
    assert world["moves"] == [NEW, "WATER GUN", "RAGE", "SCRATCH"]
    assert d.move_changes == [{"mon": MON, "forgot": "BITE",
                               "learned": NEW, "slot": 1,
                               "source": "auto-fallback"}]


# -- HM request: refused up front, auto takes over ----------------------------

def test_policy_hm_request_warns_and_falls_back(caplog):
    moves = ["BITE", "CUT", "RAGE", "SCRATCH"]
    d, world = flow_driver(moves, lambda *a: "CUT")
    with caplog.at_level(logging.WARNING, logger="trek"):
        assert d._resolve_learn_flow() is True
    hm = [w for w in policy_warnings(caplog) if "HM" in w]
    assert len(hm) == 1 and "CUT" in hm[0]
    assert world["confirm_rows"] == ["BITE"]    # CUT was never confirmed
    assert world["moves"] == [NEW, "CUT", "RAGE", "SCRATCH"]


# -- per-flow state never leaks into the next flow -----------------------------

def test_decision_state_cleared_between_flows():
    d, world = flow_driver(MOVES, lambda *a: "RAGE")
    assert d._resolve_learn_flow() is True
    assert d._learn_flow is None
    assert world["moves"][2] == NEW


# -- the DEFAULT policy: never trade damage for a status move ----------------
#
# With no learn_policy set, the old default was FORGET_PRIORITY -- a
# hand-ranked NAME list that contains damaging moves (TACKLE, BUBBLE,
# EMBER, SWIFT) and, on no match, confirmed slot 1. That is how a
# Gyarados traded HYDRO PUMP for RAIN DANCE. default_learn_policy reads
# base power out of the ROM instead.

POWERS = {"HYDRO PUMP": 120, "SURF": 95, "BITE": 60, "TACKLE": 35,
          "RAIN DANCE": 0, "LEER": 0, "SCARY FACE": 0, "FLASH": 0,
          "IRON TAIL": 100, "WATER GUN": 40}


def power_driver():
    """A Driver with just the ROM tables default_learn_policy reads."""
    from types import SimpleNamespace
    d = Driver.__new__(Driver)
    ids = {i: name for i, name in enumerate(POWERS, 1)}
    d.names = SimpleNamespace(moves=ids, items={}, species={})
    d.bdata = SimpleNamespace(moves={i: {"power": POWERS[n]}
                                     for i, n in ids.items()})
    return d


def decide(new_move, current):
    return power_driver().default_learn_policy("GATOR", new_move, current)


def test_default_declines_a_status_move_that_would_cost_damage():
    """Two attacks and nothing expendable: RAIN DANCE is not worth
    HYDRO PUMP (or anything else)."""
    assert decide("RAIN DANCE", ["HYDRO PUMP", "SURF"]) == "DECLINE"


def test_default_sacrifices_a_status_move_for_a_status_move():
    assert decide("RAIN DANCE", ["HYDRO PUMP", "SURF", "LEER"]) == "LEER"


def test_default_sacrifices_the_weakest_attack_for_a_stronger_one():
    got = decide("IRON TAIL", ["HYDRO PUMP", "SURF", "BITE", "TACKLE"])
    assert got == "TACKLE"


def test_default_declines_a_move_weaker_than_everything_it_knows():
    """Learning something worse is not an upgrade."""
    assert decide("TACKLE", ["HYDRO PUMP", "SURF", "BITE"]) == "DECLINE"


def test_default_gives_up_the_weakest_attack_only_when_damage_is_deep():
    """Three attacks and no filler: a status move can have the weakest
    one."""
    assert decide("SCARY FACE", ["HYDRO PUMP", "SURF", "BITE"]) == "BITE"


def test_default_never_names_an_hm_move():
    """The game refuses to delete HM moves and the forget menu loops on
    the refusal, so FLASH is invisible to the policy even though it is
    the obvious 0-power sacrifice."""
    assert decide("RAIN DANCE", ["HYDRO PUMP", "SURF", "FLASH"]) == "DECLINE"
    assert decide("IRON TAIL", ["FLASH", "SURF"]) == "DECLINE"


def test_default_is_deterministic_on_ties():
    """Equal power ties break on the name, so the same flow always makes
    the same sacrifice however the moveset is ordered."""
    a = decide("IRON TAIL", ["LEER", "SCARY FACE", "HYDRO PUMP"])
    b = decide("IRON TAIL", ["SCARY FACE", "LEER", "HYDRO PUMP"])
    assert a == b == "LEER"


def test_default_falls_through_to_auto_without_rom_data():
    """A bare/duck-typed driver has no Moves table; the AUTO policy must
    still run rather than the flow wedging."""
    d = Driver.__new__(Driver)
    assert d.default_learn_policy("GATOR", "RAIN DANCE", ["SURF"]) is None


def test_the_default_decides_the_real_flow_and_is_credited(caplog):
    """End to end through _resolve_learn_flow with no learn_policy set:
    the status move goes, NOT slot 1, and move_changes says who chose."""
    moves = ["BITE", "WATER GUN", "LEER", "SURF"]
    d, world = flow_driver(moves, new_move="IRON TAIL")
    ref = power_driver()
    d.names, d.bdata = ref.names, ref.bdata
    with caplog.at_level(logging.WARNING, logger="trek"):
        assert d._resolve_learn_flow() is True
    assert world["confirm_rows"] == ["LEER"]
    assert world["moves"] == ["BITE", "WATER GUN", "IRON TAIL", "SURF"]
    assert d.move_changes == [{"mon": MON, "forgot": "LEER",
                               "learned": "IRON TAIL", "slot": 3,
                               "source": "default"}]
