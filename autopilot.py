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

Rails run in deterministic code and NEVER trust the decider:
- Stuck detector: if an action marked `expect_change` leaves the expanded
  world/battle/party/screen digest unchanged for --stuck-limit consecutive
  cycles, the action fails with `{"ok": false, "error": "stuck"}` and a
  fresh decision is required.
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
{"frame", "obs_digest", "action", "ok", ...}; lifecycle/error records carry
an `"event"` key such as `"fork"`, `"checkpoint"`, `"whiteout"`, or
`"journal-error"`.

Helpers (digest, stuck check, milestone classify) are plain importable
functions:

    from autopilot import digest, classify_milestones, latest_checkpoint
"""

import argparse
from collections.abc import Mapping
import hashlib
import json
import logging
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from pydantic import ValidationError

from crystalagent import paths, registry
from crystalagent.driver import Driver
from crystalagent.rolling import RollingMemory
from crystalagent.schemas import (
    NDJSONRequest,
    validate_cycle_record,
    validate_decision,
)
# Primitives a decision may invoke: the shared registry in
# crystalagent/registry.py (same surface serve.py's `run` validates against).

# --- plain helpers -------------------------------------------------------

def digest(obs, tilemap=None):
    """Stable world/UI state used by the progress rail."""
    enemy = obs.get("enemy")
    result = {
        "map": obs.get("map"),
        "x": obs.get("x"),
        "y": obs.get("y"),
        "battle": bool(obs.get("ui", {}).get("battle")),
        "textbox": bool(obs.get("ui", {}).get("textbox")),
        "flags": sorted(k for k, v in obs.get("flags", {}).items() if v),
        "bag": sorted(obs.get("bag", {}).items()),
        "money": obs.get("money"),
        "badges": list(obs.get("badges", [])),
        "enemy": None if enemy is None else {
            key: enemy.get(key) for key in (
                "species", "name", "level", "hp", "max_hp", "types"
            )
        },
        "party": [
            {
                key: mon.get(key) for key in (
                    "species", "nick", "egg", "level", "hp", "max_hp",
                    "status"
                )
            } | {
                "moves": [
                    {key: move.get(key) for key in ("name", "pp", "max_pp")}
                    for move in mon.get("moves", [])
                ]
            }
            for mon in obs.get("party", [])
        ],
    }
    if tilemap is not None:
        result["screen"] = hashlib.blake2s(
            bytes(tilemap), digest_size=8
        ).hexdigest()
    return result


def stuck(prev_digest, new_digest):
    """True when a non-idle action produced zero observable delta."""
    return prev_digest == new_digest


def result_failure(action, result):
    """Return a failure sentence for explicit action-result markers."""
    if result is False:
        return f"{action} returned False"
    if result == "timeout":
        return f"{action} returned 'timeout'"
    if isinstance(result, Mapping):
        for marker in ("ok", "answered", "caught"):
            if result.get(marker) is False:
                detail = next(
                    (result.get(key) for key in ("reason", "error", "note")
                     if result.get(key)),
                    None,
                )
                return str(detail) if detail else (
                    f"{action} returned {marker}=False"
                )
    return None

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
        self._fork_index, self._cycle_index = self._scan_indices()

    # -- bookkeeping ------------------------------------------------------

    def _note(self, entry):
        entry.setdefault("t", datetime.now(timezone.utc)
                         .isoformat(timespec="seconds"))
        with open(self.journal, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, separators=(",", ":")) + "\n")

    def _scan_indices(self):
        fork_index = 0
        for state in paths.SAVES_DIR.glob(f"{self.session}-pre-*.state"):
            try:
                fork_index = max(
                    fork_index, int(state.stem.rsplit("-", 1)[1])
                )
            except ValueError:
                pass
        cycle_index = sum(
            1 for entry in iter_journal(self.journal)
            if not entry.get("event")
        )
        return fork_index, cycle_index

    def fork(self, tag):
        """Fork-before-risky: copy working state + .meta sidecar."""
        self._fork_index += 1
        target = paths.SAVES_DIR / (
            f"{self.session}-pre-{self._fork_index}.state"
        )
        shutil.copy2(self.d.state_path, target)
        meta = Path(f"{self.d.state_path}.meta")
        if meta.exists():
            shutil.copy2(meta, f"{target}.meta")
        self._note({"event": "fork", "file": target.name, "tag": tag,
                    "frame": self.d.emu.frame})
        return target

    def _next_checkpoint_name(self, kind):
        n = 0
        for p in paths.SAVES_DIR.glob(f"{self.session}-{kind}-*.state"):
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


    def whiteout_recovery(self):
        ckpt = latest_checkpoint(self.journal)
        if not ckpt:
            self._note({"event": "whiteout", "error": "no checkpoint known"})
            raise RuntimeError("whiteout but no journaled checkpoint")
        self.d._load_state(paths.SAVES_DIR / ckpt)
        map_name = self.d.map_name()
        if "POKECENTER" in map_name.upper():
            registry.resolve(self.d, "heal", {})
        else:
            bag = self.d.bag()
            potion = next(
                (name for name, qty in bag.items()
                 if qty > 0 and "POTION" in name.upper()),
                None,
            )
            if potion is not None:
                self.d.use_item(potion)
        obs = self.d.observe()
        self._note({"event": "whiteout", "loaded": ckpt,
                    "frame": obs["frame"]})
        return obs

    # -- one decision cycle --------------------------------------------------

    def _resolve_action(self, name, kwargs):
        """Validate against the shared registry and return its callable."""
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

        self._cycle_index += 1
        action = args.get("action") or {}
        name = action.get("name")
        kwargs = dict(action.get("kwargs") or {})
        goal = args.get("goal")
        success = args.get("success") or {}

        # Validate name, kwargs, and battle state before any risky fork.
        fn = self._resolve_action(name, kwargs)
        spec = registry.ACTIONS[name]

        sig = (name, json.dumps(kwargs, sort_keys=True), goal)
        if sig != self._last_sig:
            self.stuck_run = 0
        self._last_sig = sig

        before = self.d.observe()
        dig_before = digest(before, self.d.emu.tilemap())
        f0 = self.d.emu.frame

        if args.get("risky"):
            self.fork(f"cycle{self._cycle_index}")

        code = getattr(fn, "__code__", None)
        if code is not None and \
                "max_frames" in code.co_varnames[:code.co_argcount]:
            kwargs.setdefault("max_frames", self.budget)

        result = None
        error = None
        try:
            result = fn(**kwargs)
            if name != "settle":
                self.d.settle(max_frames=600)
        except (Exception, SystemExit) as e:
            error = f"{type(e).__name__}: {e}"

        after = self.d.observe()
        dig_after = digest(after, self.d.emu.tilemap())
        used = self.d.emu.frame - f0
        why = []
        fired_stuck = False
        wiped = bool(after["party"]) and all(
            mon["hp"] == 0 for mon in after["party"]
        )

        if wiped:
            try:
                after = self.whiteout_recovery()
                dig_after = digest(after, self.d.emu.tilemap())
                why.append("whiteout: recovered from checkpoint")
            except Exception as e:
                error = f"whiteout recovery failed: {type(e).__name__}: {e}"
                why.append("whiteout: recovery failed")
            ok = False
            self.stuck_run = 0
        else:
            explicit_failure = result_failure(name, result) if not error else None
            if explicit_failure:
                error = explicit_failure
            ok = error is None
            if ok and "map" in success and \
                    after["map"].upper() != str(success["map"]).upper():
                ok = False
                why.append(f"map {after['map']} != {success['map']}")
            if ok and "min_badges" in success and \
                    len(after["badges"]) < success["min_badges"]:
                ok = False
                why.append(f"badges {len(after['badges'])} "
                           f"< {success['min_badges']}")
            if ok and "flag" in success and \
                    not after["flags"].get(success["flag"]):
                ok = False
                why.append(f"flag unset: {success['flag']}")
            if ok and used > self.budget * 3 // 2:
                ok = False
                why.append(f"over budget ({used} > {self.budget})")

            if ok and spec.expect_change and stuck(dig_before, dig_after):
                self.stuck_run += 1
                if self.stuck_run >= self.stuck_limit:
                    ok, fired_stuck = False, True
                    why.append(
                        f"no digest delta for {self.stuck_run} cycles"
                    )
            else:
                self.stuck_run = 0

        lead = after["party"][0] if after["party"] else {}
        record = {
            "frame": after["frame"],
            "lead_lv": lead.get("level"),
            "obs_digest": dig_after,
            "action": {"name": name, "kwargs": kwargs},
            "goal": goal,
            "ok": ok,
            "used": used,
        }
        if error:
            record["error"] = error
        if why:
            record["why"] = why
        try:
            self._note(validate_cycle_record(record))
        except Exception as e:
            log = logging.getLogger("autopilot")
            log.exception("cycle journal write failed")
            try:
                self._note({
                    "event": "journal-error",
                    "frame": after.get("frame"),
                    "error": f"{type(e).__name__}: {e}",
                })
            except Exception:
                log.exception("journal-error event write failed")

        if not wiped:
            for kind in classify_milestones(before, after):
                self._checkpoint(kind, after["frame"])

        if ok:
            try:
                self.d.emu.save(self.d.state_path)
            except Exception:
                logging.getLogger("autopilot").exception(
                    "working state save failed"
                )

        try:
            outcome = "ok" if ok else (error or "; ".join(why) or "failed")
            self.mem.add(f"[{name}]{f' {goal}' if goal else ''} -> {outcome}")
            self.mem.finalize_iteration()
        except Exception:
            pass

        reply = {"id": rid, "ok": ok, "obs": compact_obs(after)}
        if fired_stuck:
            reply["error"] = "stuck"
            reply["detail"] = why[-1] if why else "no observe-digest delta"
        elif error:
            reply["error"] = error
        elif why:
            reply["error"] = "; ".join(why)
        try:
            reply["mem_tail"] = [content for _, content in self.mem.tail(3)]
        except Exception:
            pass
        return reply

    # --- stdin loop ---------------------------------------------------------


def handle_request(ap, d, req):
    """Dispatch one validated request without allowing pipe termination."""
    rid = req.get("id")
    cmd = req["cmd"]
    args = req.get("args") or {}
    try:
        if cmd == "quit":
            return {"id": rid, "ok": True, "data": "bye"}, True
        if cmd == "observe":
            return {
                "id": rid, "ok": True, "obs": compact_obs(d.observe())
            }, False
        if cmd == "screen":
            return {
                "id": rid,
                "ok": True,
                "screen": d.emu.screen_text(),
                "ui": {
                    "textbox": d.textbox(),
                    "battle": bool(d.battle()),
                },
                "frame": d.emu.frame,
            }, False
        if cmd == "memory":
            n = int(args.get("tail", 10))
            return {
                "id": rid,
                "ok": True,
                "frontier": [
                    f"[{start}-{end}]L{level}: {content}"
                    for start, end, level, content in ap.mem.frontier()
                ],
                "tail": [content for _, content in ap.mem.tail(n)],
            }, False
        if cmd == "save":
            path = args.get("path")
            if not path:
                raise ValueError("save needs {'path': <file.state>}")
            target = Path(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            d.emu.save(target)
            return {
                "id": rid,
                "ok": True,
                "saved": str(target),
                "frame": d.emu.frame,
            }, False
        if cmd == "decision":
            return ap.cycle(args, rid=rid), False
        raise ValueError(
            f"unknown cmd {cmd!r}; expected "
            "decision|observe|screen|memory|save|quit"
        )
    except (Exception, SystemExit) as e:
        logging.getLogger("autopilot").exception(
            "request %r failed", cmd
        )
        return {
            "id": rid,
            "ok": False,
            "error": f"{type(e).__name__}: {e}",
        }, False


def main():
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
    if state.resolve() == paths.DEFAULT_STATE.resolve() and not allow_default:
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
        quit_now = False
        try:
            raw = json.loads(line)
            req = NDJSONRequest.model_validate(raw).model_dump()
        except json.JSONDecodeError as e:
            resp = {"id": None, "ok": False, "error": f"bad JSON: {e}"}
        except ValidationError as e:
            first = e.errors()[0]
            resp = {
                "id": None,
                "ok": False,
                "error": f"bad request: {first['msg']} at "
                         f"{list(first['loc'])}",
            }
        else:
            resp, quit_now = handle_request(ap_, d, req)
        json.dump(resp, sys.stdout)
        sys.stdout.write("\n")
        sys.stdout.flush()
        if quit_now:
            break

    print("[autopilot] done", file=sys.stderr, flush=True)
    d.emu.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
