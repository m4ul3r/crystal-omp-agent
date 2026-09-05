"""Crystal still works. Proven by driving it, not by asserting it.

This repo grew out of a Crystal-only harness, and the deal with upstream is
that Gen 2 keeps running while Sapphire is built beside it. That promise is
easy to make and easy to break: every shared module here -- the emulator
wrapper, the charmap decode, the map/BFS layer, the adapter surface -- is used
by both, so a Sapphire-shaped change lands on Crystal without anyone noticing
until someone tries to play it.

`test_gen2_live.py` already proves Crystal BOOTS. That is not enough. Booting
exercises the ROM and the screen decode and nothing else; the layer that
actually breaks is navigation, because that is where the game-specific
assumptions live. So this lane drives a power-on cartridge through its own
intro into the overworld and then WALKS, which is the cheapest thing that
fails loudly when the shared layer drifts.

The whole file costs a couple of seconds: a fresh Crystal boot reaches
PLAYERS_HOUSE_2F in about 1.6 s, so there is no excuse for not running it.

What is deliberately NOT asserted: battles. There is no Gen-2 battle driver
and `Gen2Adapter.CAPABILITIES` says so. Pretending otherwise here would make
this file a wish rather than a regression test.
"""

import pytest

from pokeagent import gamespec
from pokeagent.adapters import base

pytestmark = pytest.mark.integration

#: Where Crystal drops a new player. Fixed by the game, so it doubles as a
#: check that the intro actually completed rather than stalling on a prompt.
BEDROOM = "PLAYERS_HOUSE_2F"
BEDROOM_START = (3, 3)

_DPAD = {"U": "up", "D": "down", "L": "left", "R": "right"}


def _step(backend, move: str) -> tuple:
    """One step, allowing for the turn-then-walk rule.

    Gen 2 spends the first press TURNING when the player is not already facing
    that way, so a single press is not a step. This is the game being itself,
    not the harness misbehaving -- and it is precisely the kind of rule a
    Sapphire-shaped driver forgets, which is why it is spelled out here rather
    than hidden behind a settle.
    """
    emu, state = backend.emu, backend.state
    before = (state.location().x, state.location().y)
    for _ in range(2):
        emu.run_sequence([([_DPAD[move]], 16), ([], 20)])
        now = (state.location().x, state.location().y)
        if now != before:
            return now
    return before


def _boot_to_overworld(backend, max_presses: int = 240):
    """Power-on -> NEW GAME -> Oak -> clock -> name -> standing in the bedroom.

    Everything in Crystal's intro advances on A, including the naming keyboard
    (which accepts the preset name), so a bounded A-mash is enough and does not
    need to model any of those screens. Bounded is the point: a loop that waits
    for the overworld forever turns a regression into a hang.
    """
    emu, state = backend.emu, backend.state
    while emu.frame < 3000:
        emu.tick(200)
    for _ in range(10):
        emu.run_sequence([(["start"], 4), ([], 40)])
        emu.run_sequence([(["a"], 4), ([], 40)])
        if any("NEW GAME" in row for row in emu.screen_text()):
            break
    else:
        pytest.fail("never reached the NEW GAME menu")
    for _ in range(max_presses):
        emu.run_sequence([(["a"], 4), ([], 24)])
        location = state.location()
        if location is not None and location.map_name == BEDROOM:
            return location
    pytest.fail("intro never handed control over in the overworld")


@pytest.fixture()
def crystal():
    """A fresh cartridge per test.

    Function-scoped for the same reason the sibling lane is: these tests DRIVE
    the machine, and a shared instance makes them order-dependent.
    """
    spec = gamespec.get("crystal")
    try:
        return base.resolve(spec).open(fresh=True)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"pokecrystal artifacts unavailable: {exc}")


def test_the_intro_hands_over_control_in_the_overworld(crystal):
    """Power-on to a standing player. If this fails, nothing below can run."""
    location = _boot_to_overworld(crystal)
    assert location.map_name == BEDROOM
    assert (location.x, location.y) == BEDROOM_START


def test_the_player_actually_walks(crystal):
    """A square walk, back to the start.

    Four steps that each move by one and return home is a stronger claim than
    any single step: it proves the direction mapping is not transposed, that
    the state layer re-reads position rather than caching it, and that the
    emulator is being advanced far enough for a step to complete.
    """
    start = _boot_to_overworld(crystal)
    here = (start.x, start.y)
    seen = [here]
    for move in ("D", "L", "U", "R"):
        seen.append(_step(crystal, move))
    assert seen[1] != seen[0], f"the player never moved: {seen}"
    assert len(set(seen)) == 4, f"expected a square walk, walked {seen}"
    assert seen[-1] == here, f"did not return to {here}: {seen}"


def test_bfs_plans_a_route_on_a_decoded_crystal_map(crystal):
    """The map layer is shared with Sapphire, so it gets exercised on Gen 2.

    Planning to the cell one south of the start is trivially short on purpose:
    the value is that `find_path` accepted a real Crystal map constant and a
    real decoded grid, which is what breaks when the collision or connection
    format is quietly reshaped for the GBA.
    """
    start = _boot_to_overworld(crystal)
    here = (start.x, start.y)
    goal = (here[0], here[1] + 1)
    path = crystal.nav.find_path(BEDROOM, here, goal)
    assert path, f"no path from {here} to {goal} on {BEDROOM}"
    assert path == ["D"], f"expected a single step south, planned {path}"

    for move in path:
        _step(crystal, move)
    location = crystal.state.location()
    assert (location.x, location.y) == goal, "walked the plan, landed elsewhere"


def test_the_state_layer_reads_a_live_cartridge(crystal):
    """Structured reads, not screen scraping.

    A brand-new save has no party and no badges, and those specific zeroes are
    the assertion: a struct read at the wrong offset returns garbage far more
    often than it returns exactly zero.
    """
    _boot_to_overworld(crystal)
    state = crystal.state
    snapshot = state.snapshot()
    assert snapshot, "empty snapshot from a running cartridge"
    assert state.party() == [], "a new game should have an empty party"
    line = state.status_line()
    assert BEDROOM in line, line
    assert "badges=0/8" in line, line


def test_sapphire_does_not_disturb_crystal_in_the_same_process(crystal):
    """The real regression risk, made concrete.

    Both cartridges get opened and driven in one interpreter -- two different
    emulator cores, two symbol tables, two charmaps. A module-level cache keyed
    on anything but the game is exactly the bug this catches, and it would show
    up as Crystal reading Sapphire's addresses.
    """
    sapphire = base.resolve(gamespec.get("sapphire")).open()
    sapphire.emu.tick(200)

    location = _boot_to_overworld(crystal)
    assert location.map_name == BEDROOM, "Sapphire displaced Crystal's read"
    assert crystal.spec.core == "pyboy"
    assert sapphire.spec.core == "mgba"
    assert crystal.emu is not sapphire.emu
