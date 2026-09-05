"""Search snapshots and travel budgets must never become caller-owned state."""

import pickle
from pathlib import Path
from types import SimpleNamespace

import pytest

from pokeagent import trek
from pokeagent.trek import Driver, TravelError, TravelInterrupted

pytestmark = pytest.mark.unit


class SnapshotEmulator:
    """A disk-backed state boundary; Driver.save/load remain real methods."""

    def __init__(self, driver, fault=None):
        self.driver = driver
        self.fault = fault
        self.frame = 0
        self.position = (0, 0)
        self.saved = []
        self.owners = []

    def save_state(self, path):
        path = Path(path)
        self.saved.append(path)
        if self.fault == "save":
            raise OSError("snapshot write failed")
        path.write_bytes(pickle.dumps((self.position, self.frame)))

    def load_state(self, path):
        if self.fault == "load":
            raise OSError("snapshot read failed")
        self.position, self.frame = pickle.loads(Path(path).read_bytes())

    def settle(self, frames):
        self.owners.append(self.driver.state_path)
        if self.fault == "search":
            raise RuntimeError("emulation failed")
        self.frame += frames


def search_driver(tmp_path, fault=None, blocked=False):
    d = object.__new__(Driver)
    d.state_path = tmp_path / "working.state"
    d.emu = SnapshotEmulator(d, fault)
    d.pos = lambda: d.emu.position
    d.map_name = lambda: "Gym"
    d.status = lambda: "Gym"
    d.gate_signature = lambda: (1,)
    d.in_battle = lambda: False
    d.scene_active = lambda: False
    d.elevation = lambda: 3
    d.settle = d.emu.settle

    def step(direction):
        if not blocked and direction == "R":
            d.emu.position = (1, 0)

    def goto(x, y, **kwargs):
        d.emu.position = (x, y)
        return True

    d.step_dir = step
    d.goto = goto
    d.nav = SimpleNamespace(
        info=lambda name: SimpleNamespace(warps=[]),
        reachable=lambda name, pos, elevation: {pos},
        grid=lambda name: [[]],
    )
    return d


@pytest.mark.parametrize("solver", ["solve_gate_maze", "solve_warp_maze"])
@pytest.mark.parametrize("outcome", ["success", "exhaustion", "search", "save", "load"])
def test_search_preserves_save_owner_and_releases_scratch(tmp_path, solver, outcome):
    fault = outcome if outcome in {"search", "save", "load"} else None
    d = search_driver(tmp_path, fault, blocked=outcome == "exhaustion")
    home = d.state_path
    target = (9, 9) if outcome == "exhaustion" else (1, 0)
    search = getattr(d, solver)
    if fault:
        error = RuntimeError if fault == "search" else OSError
        with pytest.raises(error):
            search(*target, max_nodes=8)
    else:
        assert search(*target, max_nodes=8) is (outcome == "success")
        assert d.pos() == (target if outcome == "success" else (0, 0))

    assert d.state_path == home
    assert all(owner == home for owner in d.emu.owners)
    assert d.emu.saved
    assert all(not path.parent.exists() for path in d.emu.saved)
    d.emu.fault = None
    d.emu.frame = 1234
    assert d.save() == home
    assert pickle.loads(home.read_bytes()) == (d.pos(), 1234)


def test_gate_search_already_at_target_releases_root_snapshot(tmp_path):
    d = search_driver(tmp_path)
    assert d.solve_gate_maze(0, 0)
    assert d.state_path == tmp_path / "working.state"
    assert all(not path.parent.exists() for path in d.emu.saved)


def test_gate_replay_exception_preserves_save_owner_and_releases_scratch(tmp_path):
    d = search_driver(tmp_path)
    original_step = d.step_dir
    right_steps = 0

    def fail_replay(direction):
        nonlocal right_steps
        if direction == "R":
            right_steps += 1
            if right_steps == 2:
                raise RuntimeError("replay failed")
        original_step(direction)

    d.step_dir = fail_replay
    with pytest.raises(RuntimeError, match="replay failed"):
        d.solve_gate_maze(1, 0)
    assert d.state_path == tmp_path / "working.state"
    assert all(not path.parent.exists() for path in d.emu.saved)
    assert d.save() == d.state_path


def travel_driver():
    d = object.__new__(Driver)
    d._surf_sync = lambda: None
    d.heartbeat = lambda message: None
    d.map_name = lambda: "Start"
    d.pos = lambda: (0, 0)
    d.in_battle = lambda: False
    d.scene_active = lambda: False
    d._mark_npcs = lambda name: None
    d._gate_hint = lambda name: ""
    d.nav = SimpleNamespace(route_legs=lambda *args, **kwargs: None)
    d._flight = SimpleNamespace(flyable_here=lambda: True)
    return d


@pytest.mark.parametrize("outcome", ["arrived", "expired", "no-route", "battle", "legs", "error"])
@pytest.mark.parametrize("previous", [None, 150])
def test_travel_restores_deadline_on_every_exit(monkeypatch, outcome, previous):
    monkeypatch.setattr(trek._time, "time", lambda: 100)
    d = travel_driver()
    d._journey_deadline = previous
    if outcome == "arrived":
        assert d.travel("Start", budget_s=10)
    elif outcome == "expired":
        assert not d.travel("End", budget_s=-1)
    else:
        if outcome == "battle":
            d.in_battle = lambda: True
        elif outcome == "error":
            def fail_scene():
                raise RuntimeError("scene read failed")
            d.scene_active = fail_scene
        error = {"battle": TravelInterrupted, "error": RuntimeError}.get(outcome, TravelError)
        with pytest.raises(error):
            d.travel("End", budget_s=10, max_legs=0 if outcome == "legs" else 40)
    assert d._journey_deadline == previous
    # A later unbudgeted trip must not inherit the failed trip's deadline.
    monkeypatch.setattr(trek._time, "time", lambda: 120)
    d.in_battle = lambda: False
    d.scene_active = lambda: False
    assert d.travel("Start")


@pytest.mark.parametrize("inner_budget", [None, 100, 5])
def test_nested_travel_cannot_extend_parent_budget(monkeypatch, inner_budget):
    monkeypatch.setattr(trek._time, "time", lambda: 100)
    d = travel_driver()
    observed = []

    def plan(*args, **kwargs):
        observed.append(kwargs["deadline"])
        assert d._journey_deadline == kwargs["deadline"]
        return None

    d.nav.route_legs = plan
    d._journey_deadline = 120
    with pytest.raises(TravelError):
        d.travel("End", budget_s=inner_budget)
    assert observed == [105 if inner_budget == 5 else 120]
    assert d._journey_deadline == 120


def test_unbudgeted_nested_travel_respects_expired_parent(monkeypatch):
    monkeypatch.setattr(trek._time, "time", lambda: 100)
    d = travel_driver()
    d._journey_deadline = 99
    assert not d.travel("End")
    assert d._journey_deadline == 99
