"""The ``sapphire`` command line: one savestate transition per invocation.

Port of ``crystalagent/cli.py``. Every mutating command loads the state,
advances the emulator, and writes it back, so each call is one deterministic
transition and copying the state file forks the timeline.

For anything longer than a couple of commands use the warm process
(``trek.Driver`` in a kernel, or ``serve.py``) -- booting per call costs a
second of ROM parsing and symbol loading that buys nothing.
"""

import argparse
import json
import logging
import sys

from . import paths
from .registry import ACTIONS, describe, resolve


def _driver(args):
    # Imported here: trek pulls in the whole stack, and `sym`/`actions` do
    # not need an emulator at all.
    from pokeagent.trek import Driver

    state = args.state or paths.DEFAULT_STATE
    return Driver(state)


def cmd_boot(args):
    from pokeagent.trek import Driver

    d = Driver(None, fresh=True)
    d.emu.tick(args.frames)
    out = args.state or paths.DEFAULT_STATE
    d.emu.save_state(out)
    print(f"booted to frame {d.emu.frame}, saved {out}")
    return 0


def cmd_state(args):
    d = _driver(args)
    print(json.dumps(d.observe(), indent=1, default=str))
    return 0


def cmd_status(args):
    print(_driver(args).status())
    return 0


def cmd_run(args):
    d = _driver(args)
    kwargs = json.loads(args.kwargs) if args.kwargs else {}
    try:
        result = resolve(d, args.action, kwargs)
    except ValueError as exc:
        print(f"rejected: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"ran": args.action, "result": result}, default=str))
    print(d.status(), file=sys.stderr)
    if args.action not in ("observe", "status", "find_tiles", "exits", "live_npcs"):
        d.save()
    return 0


def cmd_actions(args):
    for a in describe():
        pre = f"  [{a['precondition']}]" if a["precondition"] else ""
        req = " ".join(a["required"])
        opt = " ".join(f"[{o}]" for o in a["optional"])
        print(f"{a['name']:16} {req} {opt}{pre}")
        if a["doc"]:
            print(f"{'':16} {a['doc']}")
    return 0


def cmd_input(args):
    d = _driver(args)
    d.emu.run_sequence(args.seq)
    d.settle()
    print(d.status())
    d.save()
    return 0


def cmd_screen(args):
    d = _driver(args)
    img = d.emu.screenshot(args.png)
    print(f"{img.size[0]}x{img.size[1]} -> {args.png}" if args.png else d.status())
    return 0


def cmd_sym(args):
    from .symbols import Symbols

    for s in Symbols().find(args.pattern)[: args.limit]:
        print(f"{s.addr:08x} {'l' if s.local else 'g'} {s.size:08x} {s.name}")
    return 0


def cmd_read(args):
    d = _driver(args)
    data = d.emu.read(args.symbol, args.n)
    if args.text:
        print(d.charmap.decode(data))
    else:
        print(data.hex(" "))
    return 0


def cmd_map(args):
    d = _driver(args)
    name = args.map_name or d.map_name()
    print(d.nav.render(name, here=d.pos() if name == d.map_name() else None))
    return 0


def cmd_metrics(args):
    """How long this is taking, and how long it looks like it will take.

    Reads the event log rather than the cartridge, so it works with no ROM and
    no savestate -- the numbers are the run's history, not its current frame.
    """
    import json
    import pathlib

    from pokeagent.metrics import Metrics

    from pokeagent.metrics import METRICS_DIR

    directory = pathlib.Path(args.dir) if args.dir else METRICS_DIR
    logs = sorted(directory.glob("*.jsonl")) if directory.exists() else []
    if not logs:
        print(f"no metrics recorded under {directory}")
        return
    for log_path in logs:
        name = log_path.stem
        # Built without __init__ deliberately: reading history needs no ROM,
        # no savestate and no emulator, and requiring one would make the
        # numbers unquotable from a checkout.
        reader = object.__new__(Metrics)
        reader.path, reader.session = log_path, name
        projection = reader.projection()
        if args.json:
            print(json.dumps({"session": name, "projection": projection}))
            continue
        print(f"{name}:")
        print(f"  badges timed   {projection['badges_seen']}"
              f"   species timed {projection['species_seen']}")
        for clock, label in (("play", "in-game play time"),
                             ("real", "real elapsed time")):
            span = projection.get(f"{clock}_hours_to_eight_badges")
            shown = f"{span[0]}-{span[1]} h" if span else "not enough evidence"
            print(f"  8 badges, {label:18} {shown}")
        dex = projection.get("play_hours_to_full_dex")
        print(f"  full dex, in-game play time    "
              f"{f'{dex} h' if dex else 'not enough evidence'}")
        # The basis is printed WITH the number, always. A range from two data
        # points is worth publishing; a range from two data points quoted as
        # though it were measured is not.
        print(f"  basis: {projection['badge_basis']}; {projection['dex_basis']}")


def cmd_saves(args):
    if not paths.SAVES_DIR.exists():
        print(f"no saves directory at {paths.SAVES_DIR}")
        return 1
    for p in sorted(paths.SAVES_DIR.glob("*.state")):
        meta = p.with_suffix(p.suffix + ".meta")
        frame = "?"
        if meta.exists():
            frame = json.loads(meta.read_text()).get("frames", "?")
        print(f"{p.name:32} frame={frame}")
    return 0


def build_parser():
    ap = argparse.ArgumentParser(prog="sapphire", description=__doc__)
    ap.add_argument("--state", help="savestate to operate on")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("boot", help="power on and create a state file")
    p.add_argument("--frames", type=int, default=240)
    p.set_defaults(fn=cmd_boot)

    p = sub.add_parser("metrics", help="time-to-milestone and finish estimates")
    p.add_argument("--dir", help="metrics directory (default metrics/)")
    p.add_argument("--json", action="store_true", help="machine-readable")
    p.set_defaults(fn=cmd_metrics)
    sub.add_parser("state", help="structured state as JSON").set_defaults(fn=cmd_state)
    sub.add_parser("status", help="one-line summary").set_defaults(fn=cmd_status)
    sub.add_parser("saves", help="list checkpoints").set_defaults(fn=cmd_saves)
    sub.add_parser("actions", help="the action table").set_defaults(fn=cmd_actions)

    p = sub.add_parser("run", help="invoke a registry action")
    p.add_argument("action", choices=sorted(ACTIONS))
    p.add_argument("kwargs", nargs="?", help="JSON object of arguments")
    p.set_defaults(fn=cmd_run)

    p = sub.add_parser("input", help="run an input DSL string")
    p.add_argument("seq")
    p.set_defaults(fn=cmd_input)

    p = sub.add_parser("screen", help="framebuffer")
    p.add_argument("--png")
    p.set_defaults(fn=cmd_screen)

    p = sub.add_parser("sym", help="search the symbol table")
    p.add_argument("pattern")
    p.add_argument("-n", "--limit", type=int, default=40)
    p.set_defaults(fn=cmd_sym)

    p = sub.add_parser("read", help="read memory at a symbol")
    p.add_argument("symbol")
    p.add_argument("-n", type=int, default=16)
    p.add_argument("--text", action="store_true")
    p.set_defaults(fn=cmd_read)

    p = sub.add_parser("map", help="render a map")
    p.add_argument("map_name", nargs="?")
    p.set_defaults(fn=cmd_map)
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO, format="%(message)s"
    )
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
