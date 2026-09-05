import copy
import io
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

import autopilot
import serve
from crystalagent import paths
from crystalagent.schemas import NDJSONRequest

pytestmark = pytest.mark.unit


def observation(*, x=1, frame=100, hp=20, battle=False, map_name="NEW_BARK_TOWN"):
    return {
        "map": map_name,
        "group": 1,
        "number": 1,
        "x": x,
        "y": 2,
        "tiles": {"here": "floor"},
        "party": [{
            "species": "TOTODILE",
            "nick": "GATOR",
            "egg": False,
            "level": 12,
            "hp": hp,
            "max_hp": 30,
            "status": None,
            "moves": [{"name": "SCRATCH", "pp": 30, "max_pp": 35}],
        }],
        "bag": {"POTION": 2},
        "money": 1234,
        "badges": ["ZEPHYR"],
        "flags": {"ELM": True, "OTHER": False},
        "npcs": [],
        "sprites": [],
        "ui": {"textbox": False, "battle": battle},
        "frame": frame,
    }


class FakeEmu:
    def __init__(self):
        self.frame = 100
        self.saved = []
        self.stopped = False
        self.tilemaps = 0

    def tilemap(self):
        self.tilemaps += 1
        return [0] * 360

    def screen_text(self):
        return [""] * 18

    def save(self, path):
        self.saved.append(Path(path))

    def stop(self):
        self.stopped = True


class FakeDriver:
    def __init__(self, observations=None, result=True, state_path=None):
        self.emu = FakeEmu()
        self.state_path = Path(state_path or "work.state")
        self._observations = list(observations or [observation()])
        self.observe_calls = 0
        self.result = result
        self.calls = []
        self.loaded = []
        self.checkpoints = []

    def observe(self):
        index = min(self.observe_calls, len(self._observations) - 1)
        self.observe_calls += 1
        return copy.deepcopy(self._observations[index])

    def status(self):
        return "ready"

    def battle(self):
        return False

    def textbox(self):
        return False

    def settle(self, max_frames=600, **kwargs):
        self.calls.append(("settle", kwargs | {"max_frames": max_frames}))
        self.emu.frame += 1
        return self.result

    def press(self, seq):
        self.calls.append(("press", {"seq": seq}))
        self.emu.frame += 1
        return self.result

    def map_name(self):
        self.calls.append(("map_name", {}))
        return "ROUTE_29"

    def bag(self):
        self.calls.append(("bag", {}))
        return {}

    def use_item(self, item):
        self.calls.append(("use_item", {"item": item}))

    def _load_state(self, path):
        loaded = Path(path)
        self.loaded.append(loaded)
        self.state_path = loaded
        self.emu.frame = 500
        return loaded

    def save(self, name):
        self.checkpoints.append(name)


def make_autopilot(monkeypatch, tmp_path, driver, session="test"):
    saves = tmp_path / "saves"
    saves.mkdir(exist_ok=True)
    monkeypatch.setattr(paths, "SAVES_DIR", saves)
    return autopilot.Autopilot(
        driver, session, journal_dir=tmp_path / "journal", stuck_limit=1
    )


def test_ndjson_request_rejects_non_objects_and_non_object_args():
    with pytest.raises(ValidationError):
        NDJSONRequest.model_validate([])
    with pytest.raises(ValidationError):
        NDJSONRequest.model_validate({"cmd": "observe", "args": []})
    request = NDJSONRequest.model_validate({"cmd": "observe"})
    assert request.id is None and request.args == {}


def test_serve_load_delegates_to_driver_reload():
    driver = FakeDriver()
    reply = serve.cmd_load(driver, {"path": "relative.state"})
    assert driver.loaded == [Path("relative.state")]
    assert reply == {"loaded": "relative.state", "frame": 500}


def test_serve_malformed_request_does_not_poison_next_iteration(
        monkeypatch, tmp_path):
    driver = FakeDriver()
    stdin = io.StringIO(
        "not-json\n"
        '{"id":2,"cmd":"status"}\n'
        '{"id":3,"cmd":"quit"}\n'
    )
    stdout, stderr = io.StringIO(), io.StringIO()
    monkeypatch.setattr(serve, "Driver", lambda state: driver)
    monkeypatch.setattr(serve.sys, "argv", ["serve.py", "--state", "x.state"])
    monkeypatch.setattr(serve.sys, "stdin", stdin)
    monkeypatch.setattr(serve.sys, "stdout", stdout)
    monkeypatch.setattr(serve.sys, "stderr", stderr)

    assert serve.main() == 0

    replies = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert len(replies) == 3
    assert replies[0]["ok"] is False
    assert replies[1] == {"id": 2, "ok": True, "data": "ready"}
    assert replies[2] == {"id": 3, "ok": True, "data": "bye"}
    assert driver.emu.stopped is True


def test_digest_covers_world_battle_party_and_screen():
    base = observation()
    original = autopilot.digest(base, bytes([0] * 360))
    variants = []
    for mutate in (
        lambda o: o["bag"].update({"GREAT BALL": 1}),
        lambda o: o.__setitem__("money", 999),
        lambda o: o["badges"].append("HIVE"),
        lambda o: o.__setitem__("enemy", {
            "species": 19, "name": "RATTATA", "level": 4,
            "hp": 10, "max_hp": 10, "types": ["NORMAL"],
        }),
        lambda o: o["party"][0].update({"status": "PAR"}),
        lambda o: o["party"][0]["moves"][0].update({"pp": 29}),
    ):
        changed = copy.deepcopy(base)
        mutate(changed)
        variants.append(autopilot.digest(changed, bytes([0] * 360)))
    variants.append(autopilot.digest(base, bytes([1] * 360)))

    assert all(candidate != original for candidate in variants)
    assert len(original["screen"]) == 16
    advanced = copy.deepcopy(base)
    advanced["frame"] += 999
    advanced["sprites"] = [{"slot": 1}]
    assert autopilot.digest(advanced, bytes([0] * 360)) == original


def test_normal_cycle_observes_twice(monkeypatch, tmp_path):
    driver = FakeDriver([observation(), observation(x=2, frame=102)])
    rails = make_autopilot(monkeypatch, tmp_path, driver)

    reply = rails.cycle({
        "action": {"name": "press", "kwargs": {"seq": "A:2"}},
        "goal": "advance",
    }, rid=7)

    assert reply["ok"] is True
    assert reply["id"] == 7
    assert driver.observe_calls == 2
    assert driver.emu.tilemaps == 2
    assert driver.emu.saved == [driver.state_path]


def test_idempotent_action_bypasses_stuck_rail(monkeypatch, tmp_path):
    driver = FakeDriver([observation(), observation(frame=101)])
    rails = make_autopilot(monkeypatch, tmp_path, driver)

    reply = rails.cycle({"action": {"name": "settle"}})

    assert reply["ok"] is True
    assert reply.get("error") is None


@pytest.mark.parametrize("result,detail", [
    (False, "press returned False"),
    ("timeout", "press returned 'timeout'"),
    ({"ok": False, "reason": "blocked"}, "blocked"),
    ({"answered": False, "error": "no choice"}, "no choice"),
    ({"caught": False, "note": "escaped"}, "escaped"),
])
def test_explicit_action_failures_fail_cycle(
        monkeypatch, tmp_path, result, detail):
    driver = FakeDriver(
        [observation(), observation(x=2, frame=102)], result=result
    )
    rails = make_autopilot(monkeypatch, tmp_path, driver)

    reply = rails.cycle({
        "action": {"name": "press", "kwargs": {"seq": "A:2"}}
    })

    assert reply["ok"] is False
    assert reply["error"] == detail
    assert driver.emu.saved == []


def test_fork_indices_and_cycle_tag_are_initialized_once(
        monkeypatch, tmp_path):
    driver = FakeDriver([observation(), observation(frame=101)],
                        state_path=tmp_path / "work.state")
    driver.state_path.write_bytes(b"state")
    Path(f"{driver.state_path}.meta").write_text("{}")
    saves = tmp_path / "saves"
    saves.mkdir()
    (saves / "session-pre-4.state").write_bytes(b"old")
    monkeypatch.setattr(paths, "SAVES_DIR", saves)
    journal_dir = tmp_path / "journal"
    journal_dir.mkdir()
    journal = journal_dir / "session.jsonl"
    journal.write_text(
        '{"frame":1,"ok":true}\n'
        '{"event":"checkpoint","file":"old.state"}\n'
        '{"frame":2,"ok":true}\n'
    )
    rails = autopilot.Autopilot(driver, "session", journal_dir=journal_dir)

    reply = rails.cycle({
        "action": {"name": "settle"}, "risky": True,
    })

    assert reply["ok"] is True
    fork = saves / "session-pre-5.state"
    assert fork.read_bytes() == b"state"
    entries = list(autopilot.iter_journal(journal))
    fork_entry = next(entry for entry in entries if entry.get("event") == "fork")
    assert fork_entry["tag"] == "cycle3"
    assert rails._fork_index == 5 and rails._cycle_index == 3


def test_invalid_risky_action_never_forks(monkeypatch, tmp_path):
    driver = FakeDriver()
    rails = make_autopilot(monkeypatch, tmp_path, driver)

    reply = rails.cycle({"action": {"name": "goto"}, "risky": True})

    assert reply["ok"] is False
    assert list(paths.SAVES_DIR.glob("test-pre-*.state")) == []
    assert driver.observe_calls == 0


def test_whiteout_uses_driver_reload_and_final_state_for_reply_and_journal(
        monkeypatch, tmp_path):
    healthy = observation(frame=100, hp=20, battle=True, map_name="ROUTE_29")
    wiped = observation(frame=120, hp=0, map_name="ROUTE_29")
    recovered = observation(frame=501, hp=30, map_name="CHERRYGROVE_CITY")
    driver = FakeDriver([healthy, wiped, recovered])
    rails = make_autopilot(monkeypatch, tmp_path, driver)
    checkpoint = paths.SAVES_DIR / "good.state"
    checkpoint.write_bytes(b"checkpoint")
    rails._note({"event": "checkpoint", "file": checkpoint.name, "frame": 90})

    reply = rails.cycle({
        "action": {"name": "press", "kwargs": {"seq": "A:2"}}
    })

    assert reply["ok"] is False
    assert "whiteout" in reply["error"]
    assert reply["obs"]["map"] == "CHERRYGROVE_CITY"
    assert reply["obs"]["party"][0]["hp"] == 30
    assert driver.observe_calls == 3
    assert driver.loaded == [checkpoint]
    assert ("map_name", {}) in driver.calls and ("bag", {}) in driver.calls
    assert driver.checkpoints == []
    cycles = [e for e in autopilot.iter_journal(rails.journal)
              if not e.get("event")]
    assert cycles[-1]["frame"] == 501
    assert cycles[-1]["obs_digest"]["map"] == reply["obs"]["map"]
    assert cycles[-1]["ok"] is False


def test_cycle_schema_failure_is_isolated_after_execution(
        monkeypatch, tmp_path):
    driver = FakeDriver([observation(), observation(x=2, frame=102)])
    rails = make_autopilot(monkeypatch, tmp_path, driver)
    monkeypatch.setattr(
        autopilot, "validate_cycle_record",
        lambda record: (_ for _ in ()).throw(ValueError("bad journal")),
    )

    reply = rails.cycle({
        "action": {"name": "press", "kwargs": {"seq": "A:2"}}
    })

    assert reply["ok"] is True
    assert any(e.get("event") == "journal-error"
               for e in autopilot.iter_journal(rails.journal))


def test_autopilot_malformed_envelopes_keep_pipe_alive(monkeypatch, tmp_path):
    driver = FakeDriver()
    stdin = io.StringIO(
        "[]\n"
        '{"id":2,"cmd":"observe","args":[]}\n'
        '{"id":3,"cmd":"observe"}\n'
        '{"id":4,"cmd":"quit"}\n'
    )
    stdout, stderr = io.StringIO(), io.StringIO()
    monkeypatch.setattr(autopilot, "Driver", lambda state: driver)
    monkeypatch.setattr(
        autopilot.sys, "argv",
        ["autopilot.py", "--state", "x.state", "--journal-dir",
         str(tmp_path / "journal")],
    )
    monkeypatch.setattr(autopilot.sys, "stdin", stdin)
    monkeypatch.setattr(autopilot.sys, "stdout", stdout)
    monkeypatch.setattr(autopilot.sys, "stderr", stderr)
    monkeypatch.setattr(paths, "SAVES_DIR", tmp_path / "saves")
    monkeypatch.setattr(paths, "DEFAULT_STATE", tmp_path / "default.state")
    paths.SAVES_DIR.mkdir()

    assert autopilot.main() == 0

    replies = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert len(replies) == 4
    assert replies[0]["ok"] is False and replies[1]["ok"] is False
    assert replies[2]["id"] == 3 and replies[2]["ok"] is True
    assert replies[3] == {"id": 4, "ok": True, "data": "bye"}
    assert driver.emu.stopped is True
