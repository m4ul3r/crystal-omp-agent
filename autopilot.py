#!/usr/bin/env python3
"""autopilot.py -- rails decide-loop around one trek.Driver (Phases 3+4).

The deciding agent speaks this NDJSON protocol on stdin (one JSON object
per line, replies on stdout, serve.py-style):

  reply   : {"id": N, "ok": true|false,
             "obs": <compact observe() after the action>, ...}
            screen replies add "screen" (20x18 text rows) + "ui";
            save replies add "saved" (path) -- both are pure peeks.

  request : {"id": N, "cmd": "decision",
             "args": {"action": {"name": str, "kwargs": object},
                      "goal":   str,
                      "risky":  bool,
                      "success": {"map"?:        str,
                                  "min_badges"?: int,
                                  "flag"?:       str}}}
            {"id": N, "cmd": "save",             # force-save WRAM now
                 "args": {"path": "saves/x.state"}}
            {"id": N, "cmd": "quit"}
            {"id": N, "cmd": "observe"}          # debug peek, no journal
            {"id": N, "cmd": "screen"}           # decoded screen text + UI
            {"id": N, "cmd": "memory",           # rolling-memory view
                 "args": {"tail"?: N}}           #   (frontier + raw tail)
            {"id": N, "cmd": "quit"}

Rails run in deterministic code and NEVER trust the decider:
- Stuck detector: if a non-idle action leaves the observe digest unchanged
  (map/x/y/battle/textbox/flags/party HP) for --stuck-limit consecutive
  cycles, the action failed: reply {"ok": false, "error": "stuck"} and
  await a fresh decision.
- Fork-before-risky: a risky decision first copies the working state
  (+ .meta sidecar) to saves/<session>-pre-<n>.state -- savestate
  determinism makes retries free.
- Milestones are automatic: map entry / battle end / level-up / badge
  saves saves/<session>-<kind>-<n>.state under a NEW filename (never an
  overwrite) plus a journal event line. The checkpoint registry IS the
  journal: latest_checkpoint() scans it, no hardcoded defaults.
- Whiteout recovery (rail): all party HP at 0 -> load newest journaled
  checkpoint -> heal. Re-training is NOT automated: the level deficit is
  visible in the next observe(); the deciding loop owns the fix.

Journal: append-only journal/<session>.jsonl, one line per cycle
{"frame", "obs_digest", "action", "ok", ...}; event lines carry
"event": "checkpoint" | "whiteout".

Helpers (digest, stuck check, milestone classify) are plain importable
functions:

    from autopilot import digest, classify_milestones, latest_checkpoint
"""

import argparse
import json
import os
import shutil
import sys
from pathlib import Path
from datetime import datetime, timezone
from crystalagent import registry
from crystalagent.rolling import RollingMemory
from crystalagent.schemas import validate_cycle_record, validate_decision
sys.path.insert(0, str(Path(__file__).parent))
from trek import Driver
from crystalagent import registry

# Primitives a decision may invoke: the shared registry in
# crystalagent/registry.py (same surface serve.py's `run` validates against).
# Intentionally world-neutral actions the stuck detector must ignore.
IDLE_ACTIONS = {"settle"}

SAVES_DIR = Path("saves")


# --- plain helpers -------------------------------------------------------

def digest(obs):
    """Compact cross-cycle comparable view of one observation."""
    return {
        "map": obs.get("map"),
        "x": obs.get("x"),
        "y": obs.get("y"),
        "battle": bool(obs.get("ui", {}).get("battle")),
        "textbox": bool(obs.get("ui", {}).get("textbox")),
        "flags": sorted(k for k, v in obs.get("flags", {}).items() if v),
        "party_hp": [[m["hp"], m["max_hp"]] for m in obs.get("party", [])],
    }


def stuck(prev_digest, new_digest):
    """True when a non-idle action produced zero observable delta."""
    return prev_digest == new_digest


def classify_milestones(before, after):
    """Milestone kinds between two full observations."""
    kinds = []
    if before["map"] != after["map"]:
        kinds.append("map-entry")
    if before.get("ui", {}).get("battle") and not after.get("ui", {}).get("battle"):
        kinds.append("battle-end")
    for b, a in zip(before.get("party", []), after.get("party", [])):
        if b.get("nick") == a.get("nick") and a["level"] > b["level"]:
            kinds.append("level-up")
            break
    if len(after.get("badges", [])) > len(before.get("badges", [])):
        kinds.append("badge")
    return kinds


def compact_obs(obs):
    """observe() trimmed to what a decider needs (~half the tokens)."""
    return {
        "map": obs["map"], "x": obs["x"], "y": obs["y"],
        "party": [{k: m[k] for k in ("species", "level", "hp", "max_hp",
                                     "status")}
                  for m in obs["party"]],
        "bag": obs["bag"], "money": obs["money"], "badges": obs["badges"],
        "flags": obs["flags"],
        "ui": obs["ui"], "frame": obs["frame"],
    }


def journal_path(session, journal_dir="journal"):
    return Path(journal_dir) / f"{session}.jsonl"


def iter_journal(path):
    if not path.exists():
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue            # torn last line after a crash


def latest_checkpoint(path):
    """Newest milestone-checkpoint filename recorded in the journal."""
    found = None
    for entry in iter_journal(path):
        if entry.get("event") == "checkpoint":
            found = entry.get("file")
    return found


def best_lead_level(path):
    """Highest lead level any journal digest ever recorded."""
    best = 0
    for entry in iter_journal(path):
        lv = entry.get("lead_lv")
        if isinstance(lv, int):
            best = max(best, lv)
    return best


# --- the rails loop ------------------------------------------------------

class Autopilot:
    def __init__(self, d, session, journal_dir="journal",
                 budget=20000, stuck_limit=1):
        self.d = d
        self.session = session
        self.journal = journal_path(session, journal_dir)
        self.journal.parent.mkdir(parents=True, exist_ok=True)
        self.budget = budget
        self.stuck_limit = stuck_limit
        self.stuck_run = 0
        self._last_sig = None          # (name, kwargs, goal) of last cycle
        self.mem = RollingMemory(Path(journal_dir) / f"{session}.memory.db")
        self.stuck_run = 0
        self.cycles = self._count_forks()

    # -- bookkeeping ------------------------------------------------------

    def _note(self, entry):
        entry.setdefault("t", datetime.now(timezone.utc)
                         .isoformat(timespec="seconds"))
        with open(self.journal, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, separators=(",", ":")) + "\n")

    def _count_forks(self):
        n = 0
        for p in SAVES_DIR.glob(f"{self.session}-pre-*.state"):
            try:
                n = max(n, int(p.stem.rsplit("-", 1)[1]))
            except ValueError:
                pass
        return n

    def fork(self, tag):
        """Fork-before-risky: copy working state + .meta sidecar."""
        n = self._count_forks() + 1
        target = SAVES_DIR / f"{self.session}-pre-{n}.state"
        shutil.copy2(self.d.state_path, target)
        meta = Path(f"{self.d.state_path}.meta")
        if meta.exists():
            shutil.copy2(meta, f"{target}.meta")
        self._note({"event": "fork", "file": target.name,
                    "frame": self.d.emu.frame})
        return target

    def _next_checkpoint_name(self, kind):
        n = 0
        for p in SAVES_DIR.glob(f"{self.session}-{kind}-*.state"):
            try:
                n = max(n, int(p.stem.rsplit("-", 1)[1]))
            except ValueError:
                pass
        return f"{self.session}-{kind}-{n + 1}.state"

    def _checkpoint(self, kind, frame):
        name = self._next_checkpoint_name(kind)
        self.d.save(name)               # new filename, never overwrite
        self._note({"event": "checkpoint", "kind": kind, "file": name,
                    "frame": frame})
        return name

    # -- rails -------------------------------------------------------------

    def _load_state(self, path):
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"no such state file: {p}")
        with open(p, "rb") as f:
            self.d.emu.py.load_state(f)
        meta = Path(f"{p}.meta")
        self.d.emu._base_frames = json.loads(
            meta.read_text()).get("frames", 0) if meta.exists() else 0
        self.d.emu._start_count = self.d.emu.py.frame_count
        self.d.state_path = p
        for b in ("up", "down", "left", "right", "a", "b", "start", "select"):
            self.d.emu.py.button_release(b)
        self.d.emu.tick(10)

    def whiteout_recovery(self):
        ckpt = latest_checkpoint(self.journal)
        if not ckpt:
            self._note({"event": "whiteout", "error": "no checkpoint known"})
            raise RuntimeError("whiteout but no journaled checkpoint")
        self._load_state(SAVES_DIR / ckpt)
        obs = self.d.observe()
        self._note({"event": "whiteout", "loaded": ckpt,
                    "frame": obs["frame"]})
        if "POKECENTER" in obs["map"].upper():
            registry.resolve(self.d, "heal", {})
        elif any(v > 0 for k, v in obs["bag"].items()
                 if "POTION" in k.upper()):
            self.d.use_item(next(k for k, v in obs["bag"].items()
                                 if "POTION" in k.upper()))
        # retraining after a whiteout is a STRATEGY decision (where to
        # pace, what to fight, when to stop): the deciding loop owns it
        # via observe() + step_dir/fight -- no bundled stop-condition leg

    # -- one decision cycle --------------------------------------------------

    def _resolve_action(self, name, kwargs):
        """Validate against the shared registry, then return the bound
        callable ('heal' maps to trek.heal_pokecenter)."""
        registry.check(self.d, name, kwargs)
        return registry.callable_for(self.d, name)

    def cycle(self, args, rid=None):
        """Exception-proof shell: a bad decision must NEVER kill the
        stdin pipe -- it journals and replies ok:false instead."""
        try:
            return self._cycle(args, rid)
        except (Exception, SystemExit) as e:
            # SystemExit too -- trek._resolve_map raises it on an unknown
            # map name, and a typo'd dest_map must not kill the pipe.
            err = f"{type(e).__name__}: {e}"
            try:
                self._note({"frame": None,
                            "action": (args.get("action") or {}),
                            "ok": False,
                            "error": err})
            except Exception:
                pass
            return {"id": rid, "ok": False,
                    "error": f"decision failed: {err}"}

    def _cycle(self, args, rid=None):
        try:
            validate_decision(args)
        except Exception as e:
            err = f"decision shape: {type(e).__name__}: {e}"
            self._note({"frame": None,
                        "action": (args.get("action") or {}),
                        "ok": False, "error": err})
            return {"id": rid, "ok": False, "error": err}
        action = args.get("action") or {}
        name = action.get("name")
        kwargs = dict(action.get("kwargs") or {})
        goal = args.get("goal")
        success = args.get("success") or {}

        # A fresh decision deserves a fresh stuck budget: only the SAME
        # decision (name + kwargs + goal) repeating with no world delta
        # accumulates toward the limit.
        sig = (name, json.dumps(kwargs, sort_keys=True), goal)
        if sig != self._last_sig:
            self.stuck_run = 0
        self._last_sig = sig

        if name not in registry.ACTIONS:
            reply = {"id": rid, "ok": False,
                     "error": f"unknown action {name!r}; allowed: "
                              f"{', '.join(sorted(registry.ACTIONS))}"}
            self._note({"frame": None, "action": {"name": name},
                        "ok": False, "error": reply["error"]})
            return reply

        before = self.d.observe()
        dig_before = digest(before)
        f0 = self.d.emu.frame

        if args.get("risky"):
            self.fork(f"cycle{len(list(iter_journal(self.journal))) + 1}")

        fn = self._resolve_action(name, kwargs)
        code = getattr(fn, "__code__", None)
        if code is not None and \
                "max_frames" in code.co_varnames[:code.co_argcount]:
            kwargs.setdefault("max_frames", self.budget)

        error = None
        try:
            fn(**kwargs)
            if name != "settle":
                self.d.settle(max_frames=600)
        except Exception as e:
            error = f"{type(e).__name__}: {e}"

        after = self.d.observe()
        dig_after = digest(after)
        used = self.d.emu.frame - f0

        ok, why = False, []
        if not error:
            ok = True
            if "map" in success and \
                    after["map"].upper() != str(success["map"]).upper():
                ok = False
                why.append(f"map {after['map']} != {success['map']}")
            if "min_badges" in success and \
                    len(after["badges"]) < success["min_badges"]:
                ok = False
                why.append(f"badges {len(after['badges'])} "
                           f"< {success['min_badges']}")
            if "flag" in success and not after["flags"].get(success["flag"]):
                ok = False
                why.append(f"flag unset: {success['flag']}")
            if used > self.budget * 3 // 2:
                ok = False
                why.append(f"over budget ({used} > {self.budget})")

        fired_stuck = False
        if not error and name not in IDLE_ACTIONS \
                and stuck(dig_before, dig_after):
            self.stuck_run += 1
            if self.stuck_run >= self.stuck_limit:
                ok, fired_stuck = False, True
                why.append(f"no digest delta for {self.stuck_run} cycles")
        else:
            self.stuck_run = 0

        lead = after["party"][0] if after["party"] else {}
        record = {"frame": after["frame"], "lead_lv": lead.get("level"),
                  "obs_digest": dig_after,
                  "action": {"name": name, "kwargs": kwargs},
                  "goal": goal, "ok": ok, "used": used}
        if error:
            record["error"] = error
        if why:
            record["why"] = why
        self._note(validate_cycle_record(record))

        for kind in classify_milestones(before, after):
            self._checkpoint(kind, after["frame"])

        wiped = bool(after["party"]) and \
            all(m["hp"] == 0 for m in after["party"])
        if wiped:
            self.whiteout_recovery()
            after = self.d.observe()
            ok = False
            why.append("whiteout: recovered from checkpoint")

        # Persist progress after a good cycle: a crash must lose at most
        # one decision. Best-effort -- never fail the cycle over it.
        if ok:
            try:
                self.d.emu.save(self.d.state_path)
            except Exception as e:
                why.append(f"state save failed: {type(e).__name__}: {e}")

        # Rolling memory: one compact line per decision, so a future
        # decider re-injection reads history without the whole journal.
        try:
            outcome = "ok" if ok else (error or "; ".join(why) or "failed")
            self.mem.add(f"[{name}]{f' {goal}' if goal else ''} -> {outcome}")
            self.mem.finalize_iteration()
        except Exception:
            pass                       # memory must never break play

        reply = {"id": rid, "ok": ok, "obs": compact_obs(after)}
        if fired_stuck:
            reply["error"] = "stuck"
            reply["detail"] = why[-1] if why else "no observe-digest delta"
        elif error:
            reply["error"] = error
        elif why:
            reply["error"] = "; ".join(why)
        try:                           # short-horizon continuity for deciders
            reply["mem_tail"] = [c for _, c in self.mem.tail(3)]
        except Exception:
            pass
        return reply

    # --- stdin loop ---------------------------------------------------------

def main():
    import logging
    logging.basicConfig(stream=sys.stderr, level=logging.INFO,
                        format="%(message)s")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--state", required=True,
                    help="working savestate (a fork you own)")
    ap.add_argument("--session", default=None,
                    help="journal/checkpoint namespace "
                         "(default: state file stem)")
    ap.add_argument("--journal-dir", default="journal")
    ap.add_argument("--budget", type=int, default=20000,
                    help="per-action frame budget")
    ap.add_argument("--stuck-limit", type=int, default=1,
                    help="no-delta cycles before a non-idle action is "
                         "declared stuck")
    args = ap.parse_args()

    state = Path(args.state)
    allow_default = os.environ.get("CRYSTAL_ALLOW_DEFAULT", "") \
        .strip().lower() in ("1", "yes", "true")
    if state.resolve() == (Path("saves") / "default.state").resolve() \
            and not allow_default:
        ap.error("refusing shared saves/default.state implicitly; fork it "
                 "or set CRYSTAL_ALLOW_DEFAULT=1 deliberately")
    session = args.session or state.stem

    print(f"[autopilot] loading {state} ...", file=sys.stderr, flush=True)
    d = Driver(str(state))
    print(f"[autopilot] ready: {d.status()}", file=sys.stderr, flush=True)
    ap_ = Autopilot(d, session, args.journal_dir, args.budget,
                    args.stuck_limit)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError as e:
            resp = {"id": None, "ok": False, "error": f"bad JSON: {e}"}
        else:
            rid = req.get("id") if isinstance(req, dict) else None
            cmd = req.get("cmd")
            if cmd == "quit":
                json.dump({"id": rid, "ok": True, "data": "bye"},
                          sys.stdout)
                sys.stdout.write("\n")
                sys.stdout.flush()
                break
            if cmd == "observe":
                resp = {"id": rid, "ok": True,
                        "obs": compact_obs(d.observe())}
            elif cmd == "screen":
                # Pure peek: decoded 20x18 screen text + UI flags, no
                # journal/stuck rails. For wedged-UI diagnosis.
                resp = {"id": rid, "ok": True,
                        "screen": d.emu.screen_text(),
                        "ui": {"textbox": d.textbox(),
                               "battle": bool(d.battle())},
                        "frame": d.emu.frame}
            elif cmd == "memory":
                # Rolling-memory view for decider re-injection: summary
                # frontier plus the raw tail. Pure read, no rails.
                n = int((req.get("args") or {}).get("tail", 10))
                resp = {"id": rid, "ok": True,
                        "frontier": [f"[{s}-{e}]L{l}: {c}"
                                     for s, e, l, c in ap_.mem.frontier()],
                        "tail": [c for _, c in ap_.mem.tail(n)]}
            elif cmd == "save":
                # Force-save CURRENT WRAM (mid-battle included) to
                # args.path as .state + .meta sidecar. No rails.
                p = (req.get("args") or {}).get("path")
                if not p:
                    resp = {"id": rid, "ok": False,
                            "error": "save needs {'path': <file.state>}"}
                else:
                    target = Path(p)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    d.emu.save(target)
                    resp = {"id": rid, "ok": True,
                            "saved": str(target), "frame": d.emu.frame}
            elif cmd == "decision":
                resp = ap_.cycle(req.get("args") or {}, rid=rid)
            else:
                resp = {"id": rid, "ok": False,
                        "error": f"unknown cmd {cmd!r}; expected "
                                 f"decision|observe|screen|memory|save|quit"}
        json.dump(resp, sys.stdout)
        sys.stdout.write("\n")
        sys.stdout.flush()

    print("[autopilot] done", file=sys.stderr, flush=True)
    d.emu.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
