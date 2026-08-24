#!/usr/bin/env python3
"""Consolidated regression suite for the trek driver's anti-loop guards.

Run on throwaway forks only -- it copies saves/codex-luna-hive.state into
/tmp/trek-selftest/ and never touches saves/. Boots a PyBoy per section
(~1 s each); whole suite < ~30 s.

    .venv/bin/python scripts/trek_selftest.py

Covers:
  r7  whiteout guard (helper semantics, call-site wiring)
  r7  goto seam-cycle guard (source + live clean multi-warp)
  r7  save() frame-monotonicity refusal (+ force)
  r7  `trek verify` leg (flags, badges, exit codes, read-only)
      `trek states` smoke
  r8  scene-cell seal actually seals (was always empty)
  r8  warp-tile goal honesty (no false success from outside goal map)
  r9  whiteout detection unions play()'s 'wipe' outcome
  r9  fight() wedge diagnostics on timeout/stuck
  r9  route() filters edges whose approaches are behind armed seals
"""
import io
import shutil
import subprocess
import sys
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

T = Path("/tmp/trek-selftest")
SAVES = ROOT / "saves"
PY = ROOT / ".venv" / "bin" / "python"
FORK = "codex-luna-hive.state"


def fresh(name):
    shutil.copy(SAVES / FORK, T / name)
    shutil.copy(SAVES / f"{FORK}.meta", T / f"{name}.meta")
    import trek
    return trek.Driver(str(T / name))


def out_of(fn):
    """Capture stdout plus driver diagnostics: trek logs to the logging
    module, so attach a capturing handler on the root logger (a stderr
    swap does not intercept an already-bound StreamHandler)."""
    import logging
    buf = io.StringIO()
    err = io.StringIO()
    root = logging.getLogger()
    cap = logging.StreamHandler(err)
    cap.setFormatter(logging.Formatter("%(message)s"))
    old_level = root.level
    root.addHandler(cap)
    root.setLevel(logging.INFO)
    try:
        with redirect_stdout(buf):
            ret = fn()
    finally:
        root.removeHandler(cap)
        root.setLevel(old_level)
    return ret, buf.getvalue() + err.getvalue()

def main():
    import json
    import logging
    import trek
    from crystalagent.state import game_state

    # driver diagnostics are log records (INFO, stderr) now
    logging.basicConfig(stream=sys.stderr, level=logging.INFO,
                        format="%(message)s")
    # ---- r7: verify leg ---------------------------------------------------
    v = fresh("v.state")
    before = (T / "v.state").read_bytes()
    r = subprocess.run(
        [PY, "trek.py", "verify", str(T / "v.state"),
         "CLEARED_SLOWPOKE_WELL", "HIVE", "ZEPHYR_BADGE", "BOGUS_FLAG_X"],
        cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 1, r.stdout + r.stderr
    for want in ("CLEARED_SLOWPOKE_WELL: SET", "HIVE: SET (badge)",
                 "ZEPHYR_BADGE: SET (badge)", "BOGUS_FLAG_X: UNKNOWN"):
        assert want in r.stdout, want
    assert (T / "v.state").read_bytes() == before, "verify rewrote state"
    print("ok  verify leg (read-only, badges, exit 1)")

    # ---- r7: save frame-guard ----------------------------------------------
    g = fresh("g_cur.state")
    shutil.copy(SAVES / FORK, T / "g_target.state")
    (T / "g_target.state.meta").write_text('{"frames": 99999999}')
    try:
        g.save(str(T / "g_target.state"))
        raise AssertionError("rollback overwrite not refused")
    except RuntimeError as e:
        assert "refusing" in str(e)
    g.save(str(T / "g_target.state"), force=True)
    meta = json.loads((T / "g_target.state.meta").read_text())
    assert meta["frames"] >= 596685
    print("ok  save() frame guard (refuse + force)")

    # ---- r7: whiteout helper + wiring ---------------------------------------
    src = {n: __import__("inspect").getsource(getattr(trek.Driver, n))
           for n in ("walk", "goto", "talk_to", "travel", "fight")}
    assert src["walk"].count("_whiteout_stop") == 1
    assert src["goto"].count("_whiteout_stop") == 1
    assert src["talk_to"].count("_whiteout_stop") == 2
    assert src["travel"].count("_whiteout_stop") == 1
    w = fresh("w.state")
    w._whiteout_pending = True
    ret, log = out_of(lambda: w._whiteout_stop("t"))
    assert ret is True and "[whiteout] aborting" in log
    assert w._whiteout_pending is False
    with redirect_stdout(io.StringIO()):
        assert w._whiteout_stop("t2") is False          # consumed once
    w.whiteout_policy = "continue"
    w._whiteout_pending = True
    with redirect_stdout(io.StringIO()):
        assert w._whiteout_stop("t3") is False          # policy escape hatch
    print("ok  whiteout helper semantics + call-site wiring")

    # ---- r9: fight() unions 'wipe' outcome + wedge diagnostics --------------
    assert 'outcome == "wipe"' in src["fight"]
    assert '"timeout", "stuck"' in src["fight"] or \
        '("timeout", "stuck")' in src["fight"]
    assert '"egg"' in src["fight"] and '["player"]["money"]' in src["fight"]
    print("ok  fight() wipe-union + timeout/stuck diagnostics present")

    # ---- r8: scene seal seals -----------------------------------------------
    s = fresh("s.state")
    s._refresh_nav_blocks()
    assert s.nav.blocked.get("AZALEA_TOWN") == {(5, 10), (5, 11)}, \
        s.nav.blocked
    ret, log = out_of(lambda: s.goto(9, 5, "sealed neck",
                                     map_name="ILEX_FOREST_AZALEA_GATE"))
    assert ret is False and "no static path" in log, log
    assert "battle [" not in log and "[WHITEOUT]" not in log, log
    assert s.map_name() == "AZALEA_GYM"
    print("ok  armed-scene seal refuses chokepoint without tripping it")

    # ---- r8: warp-tile goal honesty -----------------------------------------
    ret, log = out_of(lambda: s.goto(4, 4, "pc interior",
                                     map_name="AZALEA_POKECENTER_1F"))
    assert ret is True and s.map_name() == "AZALEA_POKECENTER_1F", log
    try:
        out_of(lambda: s.goto(3, 7, "exit-door tile",
                              map_name="AZALEA_POKECENTER_1F"))
        honest = False
    except trek.TravelError:
        honest = True
    ret2, _ = out_of(lambda: None)
    if not honest:
        # acceptable alternative: returned False, or True ONLY if inside
        honest = (ret2 is False) or s.map_name() == "AZALEA_POKECENTER_1F"
    assert honest, "exit-door tile goal produced a false success again"
    ret, log = out_of(lambda: s.goto(4, 4, "re-anchor inside",
                                     map_name="AZALEA_POKECENTER_1F"))
    assert ret is True and s.map_name() == "AZALEA_POKECENTER_1F", log
    ret, log = out_of(lambda: s.goto(3, 7, "walk out through door"))
    assert ret is True and s.map_name() == "AZALEA_TOWN", log
    print("ok  warp-tile goals honest (interior ok / door tile loud / "
          "door-exit pattern ok)")

    # ---- r7: seam guard + clean multi-warp travel ---------------------------
    assert "TravelError" in src["goto"] and "edge_counts" in src["goto"]
    ret, log = out_of(lambda: s.goto(4, 4, "back in",
                                     map_name="AZALEA_POKECENTER_1F"))
    assert ret is True and "ping-pong cycle" not in log, log
    print("ok  seam guard quiet on legitimate re-crossing")

    # ---- r9: route() cost model ----------------------------------------------
    # (a) detour-ring rejection: with the direct gate approaches removed,
    # the only remaining plan is the cross-continent ring (~1500 units);
    # the cost ceiling must refuse it rather than hand back a marathon.
    r9 = fresh("r9.state")
    r9._refresh_nav_blocks = lambda: None          # freeze our manual seal
    r9.nav.blocked = {"AZALEA_TOWN": {(3, 10), (3, 11)}}
    try:
        r9.route("ILEX_FOREST_AZALEA_GATE")
        raise AssertionError("detour ring was planned and returned")
    except LookupError:
        pass
    # (b) unsealed: direct 2-transition plan, no continent detour
    r9.nav.blocked = {}
    plan = r9.route("ILEX_FOREST_AZALEA_GATE")
    assert plan, "gate route should exist unsealed"
    hops = [st.get("to") for st in plan if st["kind"] != "walk"]
    assert hops == ["AZALEA_TOWN", "ILEX_FOREST_AZALEA_GATE"], hops
    print("ok  route(): ring rejected by cost ceiling; direct plan direct")

    # ---- r9: travel() end-to-end on the new planner --------------------------
    out_of(lambda: s.goto(3, 7, "step outside for travel test"))
    ret, log = out_of(lambda: s.travel("AZALEA_POKECENTER_1F",
                                       "planner integration"))
    assert ret and s.map_name() == "AZALEA_POKECENTER_1F", log[-500:]
    assert "drift" in log or "->" in log
    print("ok  travel() multi-leg execution with landing verification")

    # ---- r7: states smoke ----------------------------------------------------
    r = subprocess.run([PY, "trek.py", "states"],
                       cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0 and "META MISSING" in r.stdout or "ok" in r.stdout
    print("ok  states table")

    print("\nALL REGRESSION CHECKS PASSED")


if __name__ == "__main__":
    T.mkdir(exist_ok=True)
    main()
