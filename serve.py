#!/usr/bin/env python3
"""Long-lived NDJSON server around ONE trek.Driver instance.

Protocol (stdin/stdout, one JSON object per line):
  request : {"id": N, "cmd": "...", "args": {...}}
  response: {"id": N, "ok": true, "data": ...}
          | {"id": N, "ok": false, "error": "..."}

Commands:
  observe {}                       -> full structured observation
  status {}                        -> human-readable status line (str)
  save {"path": P | "name": N}?   -> savestate (+ .meta); no args = save
                                      over the working state file
  load {"path": P}                 -> load savestate in-place (no reload)
  run {"name": ..., "kwargs": {}}  -> action from crystalagent.registry
  quit {}

Anything else replies ok:false and keeps serving. Exceptions reply
ok:false and keep serving. Human-readable logs go to stderr ONLY;
Driver methods that print() are contained so stdout stays pure NDJSON.

Usage: .venv/bin/python serve.py --state saves/<fork>.state
"""

import argparse
import json
import os
import logging
import sys
from crystalagent.driver import Driver
from pathlib import Path

from crystalagent.registry import resolve
from crystalagent.schemas import NDJSONRequest
from pydantic import ValidationError
# crystalagent/registry.py (same surface autopilot decisions validate against)




def cmd_observe(d):
    return d.observe()


def cmd_save(d, args):
    if "path" in args:
        target = Path(args["path"])
        target.parent.mkdir(parents=True, exist_ok=True)
        d.emu.save(target)          # writes .state AND .meta sidecar
    else:
        target = None
        d.save(args.get("name"))    # Driver.save logs via the trek logger
    return {"saved": str(target) if target else args.get("name") or str(d.state_path),
            "frame": d.emu.frame}


def cmd_load(d, args):
    path = args.get("path") or args.get("state")
    if not path:
        raise ValueError("load needs {'path': <savestate file>}")
    loaded = d._load_state(path)
    return {"loaded": str(loaded), "frame": d.emu.frame}


def cmd_run(d, args):
    name = args.get("name")
    result = resolve(d, name, args.get("kwargs") or {})
    return {"ran": name, "result": result}


HANDLERS = {
    "observe": lambda d, a: cmd_observe(d),
    "status": lambda d, a: d.status(),
    "save": cmd_save,
    "load": cmd_load,
    "run": cmd_run,
}


def handle(d, req):
    """Dispatch one parsed request dict -> response dict."""
    rid = req.get("id") if isinstance(req, dict) else None
    try:
        cmd = req.get("cmd")
        if cmd == "quit":
            return {"id": rid, "ok": True, "data": "bye"}, True
        handler = HANDLERS.get(cmd)
        if handler is None:
            raise ValueError(f"unknown cmd {cmd!r}; "
                             f"expected one of {sorted(HANDLERS)}, quit")
        args = req.get("args") or {}
        if not isinstance(args, dict):
            raise ValueError("'args' must be a JSON object")
        # Driver progress goes through the "trek" logger (stderr); stdout
        # is our only protocol channel.
        data = handler(d, args)
        return {"id": rid, "ok": True, "data": data}, False
    except Exception as e:
        print(f"[serve] error on {req!r}: {type(e).__name__}: {e}",
              file=sys.stderr, flush=True)
        return {"id": rid, "ok": False, "error": f"{type(e).__name__}: {e}"}, False


def main():
    logging.basicConfig(stream=sys.stderr, level=logging.INFO,
                        format="%(message)s")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--state", default=None,
                    help="savestate to boot (fork of a milestone; refusing "
                         "saves/default.state unless CRYSTAL_ALLOW_DEFAULT=1)")
    args = ap.parse_args()

    if args.state is None and not os.environ.get(
            "CRYSTAL_ALLOW_DEFAULT", "").strip().lower() in ("1", "yes", "true"):
        ap.error("refusing to run on shared saves/default.state implicitly; "
                 "pass --state saves/<yours>.state (or set "
                 "CRYSTAL_ALLOW_DEFAULT=1 deliberately)")

    print(f"[serve] loading {args.state} ...", file=sys.stderr, flush=True)
    d = Driver(args.state)
    print(f"[serve] ready: {d.status()}", file=sys.stderr, flush=True)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        quit_now = False
        try:
            raw = json.loads(line)
            req = NDJSONRequest.model_validate(raw).model_dump()
        except json.JSONDecodeError as e:
            resp = {"id": None, "ok": False,
                    "error": f"bad JSON: {e}"}
        except ValidationError as e:
            first = e.errors()[0]
            resp = {"id": None, "ok": False,
                    "error": f"bad request: {first['msg']} at "
                             f"{list(first['loc'])}"}
        else:
            resp, quit_now = handle(d, req)
        json.dump(resp, sys.stdout)
        sys.stdout.write("\n")
        sys.stdout.flush()
        if quit_now:
            print("[serve] quit", file=sys.stderr, flush=True)
            break
    print("[serve] done", file=sys.stderr, flush=True)
    d.emu.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
