"""Fixtures for the emulator-in-the-loop lane (`pytest -m integration`).

The contract this lane enforces (AGENTS.md gotcha 9): same savestate +
same inputs => byte-identical result, RNG included. Every scenario boots
a FORK of a milestone savestate; a test that mutates a milestone is a
worse bug than any bug it could catch, so the fixtures below fork
`claude_saves/<milestone>.state` plus its `.meta` sidecar into tmp_path,
delete the fork in teardown even on failure, and re-verify the
milestone's sha256/mtime after every fork and again at session end.

Boot cost is ~1 s per Driver; each scenario pays it explicitly rather
than sharing mutable drivers across tests (a shared driver would make
test outcomes order-dependent).
"""

import hashlib
import shutil
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


def _saves_dir():
    """Where the lane's milestone savestates live.

    `claude_saves/` is where the wren run wrote them; this checkout keeps
    them under `backup/claude_saves/`, and the lane ERRORed 16 times on
    the missing path instead of running (FUCK_I_MESSED_UP.md #53). Try
    the documented location, then the archive, then let the caller point
    at anything else with CRYSTAL_MILESTONES."""
    import os
    env = os.environ.get("CRYSTAL_MILESTONES")
    candidates = [Path(env)] if env else []
    candidates += [REPO / "claude_saves", REPO / "backup" / "claude_saves"]
    for path in candidates:
        if path.is_dir():
            return path
    return candidates[-1]


SAVES = _saves_dir()


def _digest(path):
    """sha256 + mtime_ns of a file -- either changing means 'touched'."""
    st = path.stat()
    return hashlib.sha256(path.read_bytes()).hexdigest(), st.st_mtime_ns


def _milestone_digests():
    return {p.name: _digest(p) for p in sorted(SAVES.iterdir())
            if p.suffix in (".state", ".meta")}


@pytest.fixture(scope="session")
def milestone_guard():
    """Assert NO claude_saves milestone (.state or .meta) changed during
    the whole integration run. Checked once more at session teardown so a
    mutated milestone fails the run even if the offending test passed."""
    before = _milestone_digests()
    yield
    after = _milestone_digests()
    drifted = [n for n in sorted(before) if before[n] != after.get(n)]
    assert not drifted, (
        "integration tests MUTATED milestones: "
        + ", ".join(drifted))


@pytest.fixture
def fork_driver(milestone_guard, tmp_path):
    """fork(milestone) -> Driver booted on a temp fork.

    Copies `claude_saves/<milestone>.state` AND its `.meta` sidecar into
    the test's tmp_path, boots there, and deletes every file created in
    tmp_path on teardown -- including fight()'s `.watch.state` scratch
    sidecar -- whether the test passed, failed, or errored. The source
    milestone's digest is re-checked at teardown too, so the test that
    touched it fails, not just the session-end sweep.
    """
    made = []
    sources = {}

    def _fork(milestone):
        src = SAVES / f"{milestone}.state"
        meta = Path(f"{src}.meta")
        assert src.exists() and meta.exists(), \
            f"milestone fork source missing: {src} (+ .meta)"
        dst = tmp_path / f"{milestone}.state"
        shutil.copy2(src, dst)
        shutil.copy2(meta, Path(f"{dst}.meta"))
        from trek import Driver          # lazy: unit lane never pays this
        d = Driver(str(dst))
        made.append(dst)
        sources[src.name] = _digest(src)
        sources[meta.name] = _digest(meta)
        return d

    yield _fork

    for p in tmp_path.iterdir():         # forks, .meta sidecars, watch files
        if p.is_file():
            p.unlink()
    for name, dig in sources.items():
        now = _digest(SAVES / name)
        assert now == dig, f"integration test mutated {name}"


@pytest.fixture
def route31_wild_battle():
    """Drive a fork into a live wild battle in Route 31's grass patch.

    Deterministic: the walk seeds Python's RNG (pace() draws directions
    with random.choice), and everything under that is the emulator's own
    same-state-same-input determinism. Leaves the battle UP with the
    'Wild X appeared!' pages already pressed through and wBattleMon*
    populated, ready for outlook()/fight().
    """
    def _go(d, seed=0xC1A7A1):
        import random
        random.seed(seed)
        warps = [(e["x"], e["y"]) for e in d.exits()
                 if e["kind"] == "warp"]
        assert d.take_warp(*warps[0]), d.last_warp_reason   # exit the gym
        assert d.travel("ROUTE_31") is not None
        grass = d.find_tiles("grass", "ROUTE_31")
        assert grass, "Route 31 grass patch not found by find_tiles"
        xs = [x for x, _ in grass]
        ys = [y for _, y in grass]
        box = (min(xs), max(xs), min(ys), max(ys))
        d.goto(grass[0][0], grass[0][1])
        for _ in range(8):
            res = d.pace(40, box=box)
            if res["stopped"] == "battle":
                break
        else:
            raise AssertionError("no wild encounter in 8 pace rounds")
        assert d.battle(), "pace stopped for battle but none detected"
        # page through 'Wild X appeared!' so wBattleMon* fills in
        from crystalagent.menus import battle_menu_up
        for _ in range(20):
            if battle_menu_up(d.emu.screen_text()):
                break
            d.press("A:4 .:30")
        assert battle_menu_up(d.emu.screen_text()), \
            "action menu never came up after encounter"
        return d

    return _go
