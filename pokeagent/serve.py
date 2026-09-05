#!/usr/bin/env python
"""NDJSON server around one warm Driver.

Port of the Crystal harness's ``serve.py``. One object per line on stdin, one
reply per line on stdout; every human-readable log goes to stderr so stdout
stays pure NDJSON.

    {"cmd":"observe"}
    {"cmd":"run","name":"goto","kwargs":{"x":7,"y":15}}

Commands: ``observe``, ``status``, ``actions``, ``run``, ``save``, ``load``,
``quit``. Everything that mutates the game goes through ``registry.resolve``,
so the validation rules are identical to the CLI's and to any other surface.
A malformed request gets a structured ``ok:false``, never a dead pipe.
"""

import argparse
import json
import logging
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pokeagent import paths  # noqa: E402
from pokeagent.registry import describe, resolve  # noqa: E402

log = logging.getLogger("serve")


def reply(obj):
    sys.stdout.write(json.dumps(obj, default=str) + "\n")
    sys.stdout.flush()


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default=str(paths.DEFAULT_STATE))
    ap.add_argument("--allow-default", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(message)s")

    state = Path(a.state)
    if state.resolve() == paths.DEFAULT_STATE.resolve() and not a.allow_default:
        # default.state is a shared fork point; silently mutating it cost the
        # predecessor project real progress more than once.
        sys.exit(
            "refusing to serve saves/default.state without --allow-default; "
            "fork it first (cp default.state mine.state, and the .meta too)"
        )

    from pokeagent.trek import Driver

    d = Driver(state)
    log.info("serving %s | %s", state, d.status())

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        rid = None
        try:
            req = json.loads(line)
            rid = req.get("id")
            cmd = req.get("cmd")
            args = req.get("args") or {}

            if cmd == "observe":
                reply({"id": rid, "ok": True, "data": d.observe()})
            elif cmd == "status":
                reply({"id": rid, "ok": True, "data": d.status()})
            elif cmd == "actions":
                reply({"id": rid, "ok": True, "data": describe()})
            elif cmd == "run":
                name = req.get("name") or args.get("name")
                kwargs = req.get("kwargs") or args.get("kwargs") or {}
                result = resolve(d, name, kwargs)
                reply({"id": rid, "ok": True, "data": {"ran": name, "result": result}})
            elif cmd == "save":
                path = d.save(req.get("path") or args.get("path"))
                reply({"id": rid, "ok": True,
                       "data": {"saved": str(path), "frame": d.emu.frame}})
            elif cmd == "load":
                d.load(req.get("path") or args["path"])
                reply({"id": rid, "ok": True,
                       "data": {"loaded": str(d.state_path), "frame": d.emu.frame}})
            elif cmd == "quit":
                reply({"id": rid, "ok": True, "data": "bye"})
                return 0
            else:
                reply({"id": rid, "ok": False,
                       "error": f"unknown cmd {cmd!r}; expected observe|status|"
                                "actions|run|save|load|quit"})
        except json.JSONDecodeError as exc:
            reply({"id": None, "ok": False, "error": f"bad JSON: {exc}"})
        except ValueError as exc:
            # Registry rejections are information, not crashes.
            reply({"id": rid, "ok": False, "error": str(exc)})
        except Exception as exc:  # noqa: BLE001 - one bad request must not kill the server
            log.error("%s", traceback.format_exc())
            reply({"id": rid, "ok": False, "error": f"{type(exc).__name__}: {exc}"})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
