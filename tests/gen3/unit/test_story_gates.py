"""Prologue steps must not be offered before the game can act on them.

A fresh run reached Rustboro repeatedly with "trigger the stolen Devon Goods
errand" pending. The game does not arm that coord_event until Roxanne is
beaten (VAR_RUSTBORO_STATE 2), but the step's guard was `var_lt ... 3`, which
is also true at 0 and 1. So it read as pending from the first visit, masked
the gym behind it, gave up six times a lap and trained instead -- an hour at
0 badges with the gym twenty tiles away.

Two defences are pinned here: the steps carry the game's own precondition,
and an abandoned step falls through to the BADGE rather than to grinding.
"""
import pytest

from pokeagent.quest import PROLOGUE


def _step(detail_fragment: str):
    for s in PROLOGUE:
        if detail_fragment in s.detail:
            return s
    raise AssertionError(f"no prologue step matching {detail_fragment!r}")


@pytest.mark.unit
@pytest.mark.parametrize("fragment,gate", [
    ("stolen Devon Goods errand", "FLAG_BADGE01_GET"),
    ("Devon Goods back from the thief", "FLAG_BADGE01_GET"),
    ("deliver the goods to Mr. Stone", "FLAG_BADGE01_GET"),
])
def test_the_devon_chain_waits_for_the_badge(fragment, gate):
    """None of it is reachable until Roxanne is beaten."""
    assert _step(fragment).require == gate


@pytest.mark.unit
def test_the_sail_waits_for_briney_to_exist():
    """Delivering the goods is what unhides him; before that the house is empty."""
    assert _step("sail to Dewford").require == "!FLAG_HIDE_MR_BRINEY_ROUTE104_HOUSE"


@pytest.mark.unit
@pytest.mark.parametrize("fragment", [
    "Steven's letter in Granite Cave",
    "sail off the Dewford island",
])
def test_island_steps_wait_until_the_island_is_reached(fragment):
    """Granite Cave and the return boat are both past a crossing we have not made."""
    assert _step(fragment).require == "FLAG_VISITED_DEWFORD_TOWN"


@pytest.mark.unit
def test_every_gated_step_names_a_real_condition():
    """A `require` that is never true would be a permanent silent skip."""
    for s in PROLOGUE:
        req = getattr(s, "require", None)
        if not req:
            continue
        bare = req[1:] if req.startswith("!") else req
        assert bare.startswith(("FLAG_", "VAR_", "field:", "item:")), \
            f"{s.detail!r} has an unrecognised require {req!r}"


@pytest.mark.unit
def test_skip_story_bypasses_the_prologue():
    """The whole point: ask the same question without the step we gave up on."""
    import inspect

    from pokeagent.quest import Quest

    sig = inspect.signature(Quest.next_objective)
    assert "skip_story" in sig.parameters
    assert sig.parameters["skip_story"].default is False, \
        "skipping the story must be opt-in, never the default"


@pytest.mark.unit
def test_a_blocked_prologue_does_not_hand_back_a_later_step():
    """The prologue is a SEQUENCE. An unfinished step whose gate is shut means
    the story is blocked -- not that the step after it is due.

    Skipping ahead is how a fresh run at 0 badges was told to "walk north into
    Slateport City" (a boat ride away), then to "battle Wally outside the
    Mauville gym" (two towns past that). It chased each, gave up, and ground
    for an hour with the Rustboro gym unvisited.
    """
    from pokeagent.quest import Quest

    class Step:
        def __init__(self, name, done, offered):
            self.kind, self.name, self.value = "flag_unset", name, None
            self.detail, self.map_name, self.talk = name, "M", None
            self._done, self._offered = done, offered

    early_blocked = Step("EARLY", done=False, offered=False)
    later_open = Step("LATER", done=False, offered=True)

    q = Quest.__new__(Quest)
    q.d = type("D", (), {"state": type("S", (), {
        "flag": staticmethod(lambda n: {"EARLY": False, "LATER": False}[n]),
    })()})()
    q._step_offered = lambda s: s._offered

    import pokeagent.quest as qm
    original = qm.PROLOGUE
    try:
        qm.PROLOGUE = [early_blocked, later_open]
        assert q.pending_story() is None, \
            "a shut gate blocks the story; it must not promote a later step"
        # Once the blocker opens, that same step is what comes back.
        early_blocked._offered = True
        assert q.pending_story() is early_blocked
    finally:
        qm.PROLOGUE = original


@pytest.mark.unit
def test_a_completed_step_is_stepped_over():
    """Sequencing must not stall on work already done."""
    from pokeagent.quest import Quest

    class Step:
        def __init__(self, name, done):
            self.kind, self.name, self.value = "flag_unset", name, None
            self.detail, self.map_name, self.talk = name, "M", None
            self._done = done

    done_step = Step("DONE", done=True)
    todo_step = Step("TODO", done=False)

    q = Quest.__new__(Quest)
    q.d = type("D", (), {"state": type("S", (), {
        "flag": staticmethod(lambda n: n == "DONE"),
    })()})()
    q._step_offered = lambda s: True

    import pokeagent.quest as qm
    original = qm.PROLOGUE
    try:
        qm.PROLOGUE = [done_step, todo_step]
        assert q.pending_story() is todo_step
    finally:
        qm.PROLOGUE = original
