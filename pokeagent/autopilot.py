#!/usr/bin/env python
"""The rails decide-loop: one warm Driver, an NDJSON pipe, and guard rails.

``serve.py`` executes what it is told.  ``autopilot.py`` is the same pipe
with a supervisor around it: it validates a decision before anything runs,
forks the timeline before anything risky, checkpoints milestones on its own
initiative, journals every cycle, notices when the world stopped responding,
and rolls the timeline back if the party is wiped.  The deciding agent picks
WHAT; none of the below is left to its judgement.

Protocol (one JSON object per line on stdin, one reply per line on stdout;
every log goes to stderr so stdout stays pure NDJSON, exactly as serve.py
does it)::

    {"cmd":"decision","args":{"action":{"name":"goto","kwargs":{"x":7,"y":15}},
                              "goal":"reach the lab door","risky":false,
                              "success":{"map":"LittlerootTown"}}}
    {"cmd":"observe"}                  # compact peek, no rails, no journal
    {"cmd":"screen"}                   # what owns the screen right now
    {"cmd":"memory","args":{"tail":5}} # rolling memory: frontier + raw tail
    {"cmd":"save","args":{"path":"saves/x.state"}}
    {"cmd":"quit"}

The rails, and why each one exists:

* **Validate before executing.**  A decision passes ``schemas.validate_decision``
  and then ``registry.check`` -- against a LIVE read, not a cached snapshot --
  before the emulator advances a single frame.  The journal record is written
  afterwards by necessity (it carries the outcome), but a record that fails
  its own schema is journaled WITH the failure and the rails still run: the
  Crystal harness let that exception escape and thereby skipped its own
  milestone checkpoints and whiteout recovery.
* **The stuck digest includes bag, money, party HP, position, map and badges.**
  Crystal's omitted bag and money, so a successful purchase digested as
  "stuck" and the deciding agent was told its own working action had failed.
* **Whiteout recovery reloads a checkpoint taken strictly BEFORE the wipe.**
  Milestone classification runs after the wipe check and is skipped when the
  party is down, every checkpoint records the liveness it was taken at, and
  the reloaded party is verified alive before recovery is declared.  Crystal
  could checkpoint the blacked-out party and then "recover" into it.

Journal: append-only ``journal/<session>.jsonl``.  Cycle lines are validated
by ``schemas.validate_cycle_record`` and carry wall-clock time and frame
spend; event lines carry ``event`` = fork | checkpoint | whiteout.  The
journal IS the checkpoint registry -- nothing is hardcoded, nothing is
inferred from filenames.

The helpers are plain functions, importable without an emulator::

    from autopilot import digest, classify_milestones, healthy_checkpoints
"""

import argparse
import json
import logging
import shutil
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pokeagent import paths, registry  # noqa: E402
from pokeagent.rolling import RollingMemory  # noqa: E402
from pokeagent.schemas import (  # noqa: E402
    SchemaError,
    validate_cycle_record,
    validate_decision,
    validate_request,
)

log = logging.getLogger("autopilot")

COMMANDS = ("decision", "observe", "screen", "memory", "save", "quit")

#: Actions that legitimately leave the world exactly as they found it, so a
#: zero-delta digest after one of them is not evidence of anything.  Checked
#: against the registry at import time: a rename there must not silently turn
#: a read into a "stuck" report here.
READ_ONLY_ACTIONS = frozenset(
    {
        "observe", "status", "map_view", "find_tiles", "exits", "live_npcs",
        "battle_frame", "outlook", "recommend", "missables", "field_moves",
        "needs_flash",
    }
)
IDLE_ACTIONS = READ_ONLY_ACTIONS | {"settle", "drain_scene", "save"}
_drifted = IDLE_ACTIONS - set(registry.ACTIONS)
if _drifted:
    raise RuntimeError(
        f"IDLE_ACTIONS names {', '.join(sorted(_drifted))}, which registry.ACTIONS "
        "does not define; the two lists have drifted"
    )


# --- plain helpers ---------------------------------------------------------

def digest(obs: dict) -> dict:
    """A cross-cycle comparable view of one observation.

    Everything an action could plausibly move belongs here.  Crystal's
    version carried map/position/UI/flags/party-HP only, so ``mart_buy``
    spending money on items produced a byte-identical digest and the rails
    reported the successful purchase as stuck (its CODE_REVIEW_PLAN P3).
    Bag, money, badges and the on-screen message are therefore included.
    """
    loc = obs.get("location") or {}
    ui = obs.get("ui") or {}
    player = obs.get("player") or {}
    bag = obs.get("bag") or {}
    return {
        "map": loc.get("map"),
        "x": loc.get("x"),
        "y": loc.get("y"),
        "facing": loc.get("facing"),
        "battle": bool(ui.get("battle")),
        "dialog": bool(ui.get("dialog")),
        "scene": bool(ui.get("scene")),
        "message": ui.get("message"),
        "money": player.get("money"),
        "badges": list(player.get("badges") or []),
        # Sorted so two equal bags always compare equal regardless of the
        # order the pockets were read in.
        "bag": {
            pocket: dict(sorted((items or {}).items()))
            for pocket, items in sorted(bag.items())
        },
        "party": [
            [m.get("nickname"), m.get("level"), m.get("hp"), m.get("max_hp"),
             m.get("status")]
            for m in obs.get("party") or []
        ],
    }


def stuck(prev_digest: dict, new_digest: dict) -> bool:
    """True when an action that should have moved the world did not."""
    return prev_digest == new_digest


def party_alive(obs: dict) -> bool:
    """At least one non-egg party member still standing.

    Eggs are excluded deliberately: an egg's HP is not a fighting resource,
    and treating a party of eggs as healthy would hide a real whiteout.
    """
    mons = [m for m in (obs.get("party") or []) if not m.get("egg")]
    return bool(mons) and any((m.get("hp") or 0) > 0 for m in mons)


def party_wiped(obs: dict) -> bool:
    mons = [m for m in (obs.get("party") or []) if not m.get("egg")]
    return bool(mons) and not party_alive(obs)


def classify_milestones(before: dict, after: dict) -> list[str]:
    """The meaningful transitions between two observations."""
    kinds = []
    if (before.get("location") or {}).get("map") != (after.get("location") or {}).get("map"):
        kinds.append("map-entry")
    if (before.get("ui") or {}).get("battle") and not (after.get("ui") or {}).get("battle"):
        kinds.append("battle-end")
    for b, a in zip(before.get("party") or [], after.get("party") or []):
        # Match by nickname: a switch reorders the party and index-wise
        # comparison would then invent a level-up out of the reordering.
        if b.get("nickname") == a.get("nickname") and (a.get("level") or 0) > (b.get("level") or 0):
            kinds.append("level-up")
            break
    before_badges = len(((before.get("player") or {}).get("badges")) or [])
    after_badges = len(((after.get("player") or {}).get("badges")) or [])
    if after_badges > before_badges:
        kinds.append("badge")
    return kinds


def compact_obs(obs: dict) -> dict:
    """``observe()`` trimmed to what a decider actually reasons over.

    The full snapshot carries every NPC and every task name; this is the
    half a decision needs, which is the half that fits in a prompt.
    """
    loc = obs.get("location") or {}
    ui = obs.get("ui") or {}
    player = obs.get("player") or {}
    out = {
        "map": loc.get("map"),
        "x": loc.get("x"),
        "y": loc.get("y"),
        "facing": loc.get("facing"),
        "party": [
            {k: m.get(k) for k in ("species", "nickname", "level", "hp", "max_hp", "status")}
            for m in obs.get("party") or []
        ],
        "bag": obs.get("bag") or {},
        "money": player.get("money"),
        "badges": player.get("badges") or [],
        "ui": {
            "battle": ui.get("battle"),
            "dialog": ui.get("dialog"),
            "scene": ui.get("scene"),
            "message": ui.get("message"),
            "callback": ui.get("callback"),
        },
        "tiles": obs.get("tiles") or {},
        "frame": obs.get("frame"),
    }
    if obs.get("battle"):
        out["battle"] = obs["battle"]
    return out


def journal_path(session: str, journal_dir="journal") -> Path:
    return Path(journal_dir) / f"{session}.jsonl"


def iter_journal(path):
    """Every parseable line, oldest first. A torn final line after a kill is
    skipped rather than fatal -- the journal is append-only and the tear is
    always at the end."""
    path = Path(path)
    if not path.exists():
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                log.warning("skipping unparseable journal line in %s", path.name)


def healthy_checkpoints(path, before_frame=None) -> list[str]:
    """Checkpoint filenames safe to roll back to, newest first.

    Two conditions, both required.  ``party_alive`` was recorded from a fresh
    observation at the moment the checkpoint was written, and ``frame`` must
    be strictly earlier than the frame at which the wipe was seen.  Crystal
    took neither: it checkpointed the blacked-out party on the very
    ``battle-end`` transition that wiped it and then reloaded that.
    """
    out = []
    for entry in iter_journal(path):
        if entry.get("event") != "checkpoint" or not entry.get("file"):
            continue
        if not entry.get("party_alive"):
            continue
        frame = entry.get("frame")
        if before_frame is not None and not (isinstance(frame, int) and frame < before_frame):
            continue
        out.append(entry["file"])
    return out[::-1]


# --- the rails loop --------------------------------------------------------

class Autopilot:
    """One decision cycle at a time, with the rails around it."""

    def __init__(self, driver, session: str, journal_dir="journal",
                 saves_dir=None, budget: int = 20000, stuck_limit: int = 1):
        self.d = driver
        self.session = session
        self.saves = Path(saves_dir) if saves_dir else paths.SAVES_DIR
        self.saves.mkdir(parents=True, exist_ok=True)
        self.journal = journal_path(session, journal_dir)
        self.journal.parent.mkdir(parents=True, exist_ok=True)
        self.budget = budget
        self.stuck_limit = stuck_limit
        self.stuck_run = 0
        self._last_sig = None  # (name, kwargs, goal) of the previous cycle
        # The file this session owns. A rollback repoints Driver.state_path,
        # and if we did not put it back the next autosave would overwrite a
        # shared milestone -- the exact accident AGENTS.md forbids.
        self.work_state = Path(self.d.state_path) if self.d.state_path else None
        self.mem = RollingMemory(Path(journal_dir) / f"{session}.memory.db")

    # -- bookkeeping --------------------------------------------------------

    def _note(self, entry: dict) -> dict:
        entry.setdefault("t", datetime.now(timezone.utc).isoformat(timespec="seconds"))
        with open(self.journal, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, separators=(",", ":"), default=str) + "\n")
        return entry

    def _journal_cycle(self, record: dict) -> dict:
        """Write one cycle line. Schema drift is recorded, never raised.

        This runs after the action executed, so an exception here would skip
        milestone checkpointing and whiteout recovery for a cycle that
        already changed the world. Crystal did exactly that.
        """
        try:
            validate_cycle_record(record)
        except SchemaError as exc:
            record["record_schema_error"] = str(exc)
            log.error("cycle record failed its own schema: %s", exc)
        return self._note(record)

    def _next_name(self, kind: str) -> str:
        """``<session>-<kind>-<n+1>.state``: a new filename, never a rewrite."""
        n = 0
        for p in self.saves.glob(f"{self.session}-{kind}-*.state"):
            try:
                n = max(n, int(p.stem.rsplit("-", 1)[1]))
            except ValueError:
                continue
        return f"{self.session}-{kind}-{n + 1}.state"

    def fork(self) -> Path:
        """Fork-before-risky: copy the working state and its .meta sidecar.

        A plain file copy, not a fresh ``save_state``: the point is a
        byte-identical retry point for the state as it stands, and the meta
        must travel with it or the load refuses on provenance (gotcha 15).
        """
        if self.work_state is None:
            raise RuntimeError("cannot fork: this session has no working state file")
        target = self.saves / self._next_name("pre")
        self.d.save(self.work_state)  # fork what is live, not what is on disk
        shutil.copy2(self.work_state, target)
        meta = Path(f"{self.work_state}.meta")
        if meta.exists():
            shutil.copy2(meta, f"{target}.meta")
        self._note({"event": "fork", "file": target.name, "frame": self.d.emu.frame})
        return target

    def checkpoint(self, kind: str, obs: dict) -> str:
        """Save a milestone under a fresh name and register it in the journal.

        ``party_alive`` is recorded from the observation the checkpoint was
        taken at, because that is what makes the entry usable as a rollback
        target later.
        """
        name = self._next_name(kind)
        self.d.save(self.saves / name)
        self._note({
            "event": "checkpoint", "kind": kind, "file": name,
            "frame": obs.get("frame"), "map": (obs.get("location") or {}).get("map"),
            "party_alive": party_alive(obs),
        })
        return name

    # -- rails --------------------------------------------------------------

    def whiteout_recovery(self, wipe_frame: int) -> str:
        """Roll back to the newest checkpoint taken while the party lived.

        Fails loudly.  If no journaled checkpoint qualifies, or none of them
        loads into a living party, the caller gets an exception and the
        journal gets the reason -- silently continuing on a wiped party would
        hand the deciding agent a game it cannot win and no way to know.
        """
        candidates = healthy_checkpoints(self.journal, before_frame=wipe_frame)
        if not candidates:
            reason = (
                f"party wiped at frame {wipe_frame} and no journaled checkpoint "
                f"was taken with a living party before it ({self.journal})"
            )
            self._note({"event": "whiteout", "frame": wipe_frame, "error": reason})
            raise RuntimeError(reason)

        tried = []
        for name in candidates:
            p = self.saves / name
            if not p.exists():
                tried.append(f"{name}: missing from {self.saves}")
                continue
            self.d.load(p)
            # load() repoints state_path at the milestone; put it back before
            # anything can autosave over shared history.
            if self.work_state is not None:
                self.d.state_path = self.work_state
            self.d.settle(max_frames=600)
            obs = self.d.observe()
            if not party_alive(obs):
                tried.append(f"{name}: party still wiped after load")
                continue
            if self.work_state is not None:
                self.d.save(self.work_state)
            self._note({
                "event": "whiteout", "frame": wipe_frame, "recovered": name,
                "at_frame": obs.get("frame"),
                "map": (obs.get("location") or {}).get("map"),
                "rejected": tried,
            })
            return name

        reason = (
            f"party wiped at frame {wipe_frame}; every candidate checkpoint "
            f"failed: {'; '.join(tried)}"
        )
        self._note({"event": "whiteout", "frame": wipe_frame, "error": reason})
        raise RuntimeError(reason)

    def _check_success(self, after: dict, success: dict) -> list[str]:
        """The decider's own success criteria, checked against WRAM."""
        why = []
        want_map = success.get("map")
        if want_map is not None:
            got = (after.get("location") or {}).get("map") or ""
            if got.upper() != str(want_map).upper():
                why.append(f"map is {got}, wanted {want_map}")
        want_badges = success.get("min_badges")
        if want_badges is not None:
            got = len((after.get("player") or {}).get("badges") or [])
            if got < want_badges:
                why.append(f"{got} badges, wanted at least {want_badges}")
        flag = success.get("flag")
        if flag is not None:
            try:
                if not self.d.state.flag(flag):
                    why.append(f"flag {flag} is still clear")
            except Exception as exc:
                why.append(f"flag {flag} unreadable: {type(exc).__name__}: {exc}")
        return why

    # -- one decision cycle -------------------------------------------------

    def cycle(self, args: dict, rid=None) -> dict:
        """Exception-proof shell: a bad decision replies ok:false, and the
        pipe survives.  SystemExit is caught too -- a typo'd map name reaches
        ``sys.exit`` inside the router, and that must not kill the server."""
        try:
            return self._cycle(args, rid)
        except (Exception, SystemExit) as exc:
            err = f"{type(exc).__name__}: {exc}"
            log.error("%s", traceback.format_exc())
            try:
                self._note({"event": "cycle-crash", "action": (args or {}).get("action"),
                            "error": err})
            except Exception:  # noqa: BLE001 - journaling must not mask the reply
                pass
            return {"id": rid, "ok": False, "error": f"decision failed: {err}"}

    def _reject(self, rid, action, error: str) -> dict:
        """A decision that never ran. Journaled as a cycle so the journal has
        one line per decision, with used=0 saying nothing was spent."""
        self._journal_cycle({
            "t": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "wall_s": 0.0,
            "frame": self.d.emu.frame,
            "used": 0,
            "action": action,
            "goal": None,
            "ok": False,
            "digest": {},
            "lead_level": None,
            "error": error,
        })
        return {"id": rid, "ok": False, "error": error}

    def _cycle(self, args: dict, rid=None) -> dict:
        t0, wall0 = datetime.now(timezone.utc), time.monotonic()

        # -- validate BEFORE the emulator advances a frame ------------------
        try:
            validate_decision(args)
        except SchemaError as exc:
            # Journal as much of the rejected action as is safely readable:
            # the payload just failed its shape check, so nothing in it may
            # be assumed.
            raw = args.get("action") if isinstance(args, dict) else None
            raw = raw if isinstance(raw, dict) else {}
            name = raw.get("name") if isinstance(raw.get("name"), str) else "?"
            kwargs = raw.get("kwargs") if isinstance(raw.get("kwargs"), dict) else {}
            return self._reject(rid, {"name": name, "kwargs": kwargs}, str(exc))

        action = args["action"]
        name = action["name"]
        kwargs = dict(action.get("kwargs") or {})
        goal = args.get("goal")
        success = args.get("success") or {}

        try:
            act = registry.check(self.d, name, kwargs)
        except ValueError as exc:
            # Registry rejections are information, not crashes: the sentence
            # names what was wrong and the decider can fix it next cycle.
            return self._reject(rid, {"name": name, "kwargs": kwargs}, str(exc))

        # A fresh decision deserves a fresh stuck budget: only the SAME
        # decision repeating with no world delta accumulates toward the limit.
        sig = (name, json.dumps(kwargs, sort_keys=True, default=str), goal)
        if sig != self._last_sig:
            self.stuck_run = 0
        self._last_sig = sig

        before = self.d.observe()
        dig_before = digest(before)
        f0 = self.d.emu.frame

        forked = None
        if args.get("risky"):
            forked = self.fork().name

        if "max_frames" in act.optional:
            kwargs.setdefault("max_frames", self.budget)

        error = None
        result = None
        action_failed = False
        failure_reason = None
        try:
            result = registry.callable_for(self.d, name)(**kwargs)
            # Boolean queries (notably needs_flash) may legitimately say no.
            action_failed = result is False and name not in READ_ONLY_ACTIONS
            if action_failed:
                reason_name = {
                    "walk": "step", "step_dir": "step", "take_warp": "warp",
                    "talk_to": "talk", "advance_scene": "scene",
                }.get(name, name)
                reason = getattr(self.d, f"last_{name}_reason", None)
                reason = reason or getattr(self.d, f"last_{reason_name}_reason", None)
                failure_reason = f"{name} failed: {reason or 'driver returned False'}"
            if name not in IDLE_ACTIONS:
                self.d.settle(max_frames=600)
        except (Exception, SystemExit) as exc:
            error = f"{type(exc).__name__}: {exc}"

        after = self.d.observe()
        dig_after = digest(after)
        used = self.d.emu.frame - f0

        why = []
        ok = not error
        if action_failed:
            why.append(failure_reason)
        if not error:
            why += self._check_success(after, success)
            if used > self.budget * 3 // 2:
                why.append(f"spent {used} frames on a {self.budget}-frame budget")
            ok = not why

        fired_stuck = False
        if not error and name not in IDLE_ACTIONS and stuck(dig_before, dig_after):
            self.stuck_run += 1
            if self.stuck_run >= self.stuck_limit:
                ok, fired_stuck = False, True
                why.append(
                    f"{name} changed nothing observable for {self.stuck_run} "
                    f"consecutive cycles"
                )
        else:
            self.stuck_run = 0

        wiped = party_wiped(after)
        if wiped:
            ok = False
            why.append("party wiped")
        lead = next((m for m in after.get("party") or [] if not m.get("egg")), {})
        record = {
            "t": t0.isoformat(timespec="seconds"),
            "wall_s": round(time.monotonic() - wall0, 3),
            "frame": after.get("frame"),
            "used": used,
            "action": {"name": name, "kwargs": kwargs},
            "goal": goal,
            "ok": ok,
            "digest": dig_after,
            "lead_level": lead.get("level"),
        }
        if error:
            record["error"] = error
        if why:
            record["why"] = why
        self._journal_cycle(record)

        # -- post-execution rails -------------------------------------------
        recovered = None
        checkpoints = []
        if wiped:
            # Deliberately BEFORE milestone classification: the battle that
            # wiped the party ends, and checkpointing that transition would
            # write the blacked-out party into the rollback registry.
            recovered = self.whiteout_recovery(after.get("frame"))
            after = self.d.observe()
            ok = False
            why.append(f"party wiped; rolled back to {recovered}")
        else:
            for kind in classify_milestones(before, after):
                checkpoints.append(self.checkpoint(kind, after))

        # Persist progress after a good cycle: a crash then costs at most one
        # decision. Best effort -- never fail a good cycle over the save.
        if ok and self.work_state is not None:
            try:
                self.d.save(self.work_state)
            except Exception as exc:  # noqa: BLE001
                why.append(f"state save failed: {type(exc).__name__}: {exc}")

        # Rolling memory: one line per decision, so a later decider reads the
        # story without replaying the journal.
        outcome = "ok" if ok else (error or "; ".join(why) or "failed")
        self.mem.add(f"[{name}]{' ' + goal if goal else ''} -> {outcome}")
        self.mem.finalize_iteration()
        if self.mem.last_fold_reason:
            log.warning("rolling memory: %s", self.mem.last_fold_reason)

        reply = {"id": rid, "ok": ok, "obs": compact_obs(after), "used": used,
                 "result": result}
        if fired_stuck:
            reply["error"] = "stuck"
        elif error:
            reply["error"] = error
        elif why:
            reply["error"] = "; ".join(why)
        if why:
            reply["why"] = why
        if forked:
            reply["fork"] = forked
        if checkpoints:
            reply["checkpoints"] = checkpoints
        if recovered:
            reply["recovered"] = recovered
        reply["mem_tail"] = [c for _, c in self.mem.tail(3)]
        return reply


# --- stdin loop ------------------------------------------------------------

def reply(obj):
    sys.stdout.write(json.dumps(obj, default=str) + "\n")
    sys.stdout.flush()


def serve(pilot, driver, stream=sys.stdin) -> int:
    """The NDJSON loop. One bad request must never kill the pipe."""
    for line in stream:
        line = line.strip()
        if not line:
            continue
        rid = None
        try:
            req = json.loads(line)
            rid = req.get("id") if isinstance(req, dict) else None
            validate_request(req, COMMANDS)
            cmd = req["cmd"]
            args = req.get("args") or {}

            if cmd == "quit":
                reply({"id": rid, "ok": True, "data": "bye"})
                return 0
            if cmd == "decision":
                reply(pilot.cycle(args, rid=rid))
            elif cmd == "observe":
                reply({"id": rid, "ok": True, "obs": compact_obs(driver.observe())})
            elif cmd == "screen":
                # Sapphire has no decodable flat text layer (emu.py:276), so
                # "what is on screen" is answered by the engine's own truth:
                # the expanded message plus the callback and tasks that own
                # input. A framebuffer PNG is written only if asked for.
                out = {
                    "id": rid, "ok": True,
                    "message": driver.state.message(),
                    "ui": {
                        "battle": driver.in_battle(),
                        "dialog": driver.dialog_open(),
                        "scene": driver.scene_active(),
                        "callback": driver.state.callback_name(),
                        "tasks": driver.state.tasks(),
                    },
                    "frame": driver.emu.frame,
                }
                if args.get("path"):
                    target = Path(args["path"])
                    target.parent.mkdir(parents=True, exist_ok=True)
                    driver.emu.screenshot(target)
                    out["screenshot"] = str(target)
                reply(out)
            elif cmd == "memory":
                n = int(args.get("tail", 10))
                reply({
                    "id": rid, "ok": True,
                    "frontier": [f"[{s}-{e}]L{lv}: {c}"
                                 for s, e, lv, c in pilot.mem.frontier()],
                    "tail": [c for _, c in pilot.mem.tail(n)],
                })
            elif cmd == "save":
                path = args.get("path")
                if not path:
                    reply({"id": rid, "ok": False,
                           "error": "save needs {'path': 'saves/<name>.state'}"})
                else:
                    target = Path(path)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    driver.emu.save_state(target)
                    reply({"id": rid, "ok": True, "saved": str(target),
                           "frame": driver.emu.frame})
        except json.JSONDecodeError as exc:
            reply({"id": None, "ok": False, "error": f"bad JSON: {exc}"})
        except (SchemaError, ValueError) as exc:
            reply({"id": rid, "ok": False, "error": str(exc)})
        except Exception as exc:  # noqa: BLE001 - one bad request, not a dead pipe
            log.error("%s", traceback.format_exc())
            reply({"id": rid, "ok": False, "error": f"{type(exc).__name__}: {exc}"})
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--state", required=True, help="working savestate (a fork you own)")
    ap.add_argument("--allow-default", action="store_true")
    ap.add_argument("--session", default=None,
                    help="journal/checkpoint namespace (default: the state file stem)")
    ap.add_argument("--journal-dir", default="journal")
    ap.add_argument("--saves-dir", default=None)
    ap.add_argument("--budget", type=int, default=20000, help="per-action frame budget")
    ap.add_argument("--stuck-limit", type=int, default=1,
                    help="no-delta cycles before a non-idle action is called stuck")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(message)s")

    state = Path(a.state)
    if state.resolve() == paths.DEFAULT_STATE.resolve() and not a.allow_default:
        # default.state is a shared fork point; silently mutating it cost the
        # predecessor project real progress more than once.
        sys.exit(
            "refusing to drive saves/default.state without --allow-default; "
            "fork it first (cp default.state mine.state, and the .meta too)"
        )

    from pokeagent.trek import Driver

    d = Driver(state)
    session = a.session or state.stem
    log.info("autopilot %s | %s", state, d.status())
    pilot = Autopilot(d, session, a.journal_dir, a.saves_dir, a.budget, a.stuck_limit)
    rc = serve(pilot, d)
    log.info("autopilot done @%d", d.emu.frame)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
