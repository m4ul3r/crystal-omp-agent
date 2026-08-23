"""crystal -- drive Pokémon Crystal headlessly, built for agents.

The emulator's full machine state lives in a savestate file. Every mutating
command loads it, advances the machine, and writes it back, so each CLI call
is one deterministic transition -- copy the file to fork a timeline.
"""

import argparse
import json
import shutil
import sys
from pathlib import Path

from . import paths
from .charmap import Charmap
from .emu import Crystal, parse_sequence, InputError
from .names import Names
from .state import game_state, status_line


def _open(args, need_state=True, want_names=True):
    if not paths.ROM.exists():
        sys.exit(f"ROM not found at {paths.ROM}; build it with `make` (see INSTALL.md)")
    from .symfile import Symbols
    charmap = Charmap(paths.CHARMAP)
    sym = Symbols(paths.SYM)
    state = Path(args.state)
    if need_state and not state.exists():
        sys.exit(f"no state at {state}; run `crystal boot` first")
    emu = Crystal(paths.ROM, sym, charmap, state if need_state else None)
    names = Names(paths.ROM, sym, charmap, paths.MAP_CONSTANTS) \
        if want_names else None
    return emu, names, state


def _observe(emu, names, args):
    if not getattr(args, "quiet", False):
        print("\n".join(emu.screen_text()))
        print("--")
    print(status_line(game_state(emu, names)))


def _quiet_opt(p):
    """Re-allow -q after the subcommand (argparse would otherwise reject it;
    SUPPRESS keeps a subcommand's absence from clobbering the global flag)."""
    p.add_argument("--quiet", "-q", action="store_true",
                   default=argparse.SUPPRESS, help=argparse.SUPPRESS)


def cmd_boot(args):
    state = Path(args.state)
    if state.exists() and not args.force:
        sys.exit(f"{state} exists; use --force to restart from power-on")
    emu, names, _ = _open(args, need_state=False)
    emu.tick(args.frames)
    emu.save(state)
    _observe(emu, names, args)
    emu.stop()


def cmd_run(args):
    emu, names, state = _open(args)
    emu.tick(args.frames)
    emu.save(state)
    _observe(emu, names, args)
    emu.stop()


def cmd_input(args):
    emu, names, state = _open(args)
    try:
        steps = parse_sequence(" ".join(args.sequence))
    except InputError as e:
        sys.exit(str(e))
    if not args.until:
        advanced = emu.run_sequence(steps)
        emu.save(state)
        if not args.quiet:
            print(f"[advanced {advanced} frames]")
        _observe(emu, names, args)
        emu.stop()
        return
    start, found = emu.frame, False
    while emu.frame - start < args.max_frames:
        emu.run_sequence(steps)
        if emu.screen_contains(args.until):
            found = True
            break
    emu.save(state)
    print(f"[{'found' if found else 'NOT FOUND'} {args.until!r} "
          f"after {emu.frame - start} frames]")
    _observe(emu, names, args)
    emu.stop()
    if not found:
        sys.exit(1)


def cmd_saves(_args):
    for f in sorted(paths.SAVES_DIR.glob("*.state")):
        meta = Path(f"{f}.meta")
        frames = json.loads(meta.read_text()).get("frames", "?") \
            if meta.exists() else "?"
        print(f"{f.name:32} frames={frames}")


def cmd_mash(args):
    """Press a button repeatedly until text appears on screen (or max frames)."""
    emu, names, state = _open(args)
    steps = parse_sequence(f"{args.button}:2 .:{args.every}")
    start, found = emu.frame, False
    while emu.frame - start < args.max_frames:
        emu.run_sequence(steps)
        if args.until and emu.screen_contains(args.until):
            found = True
            break
    emu.save(state)
    if args.until:
        print(f"[{'found' if found else 'NOT FOUND'} {args.until!r} "
              f"after {emu.frame - start} frames]")
    _observe(emu, names, args)
    emu.stop()
    if args.until and not found:
        sys.exit(1)


def cmd_screen(args):
    emu, _, _ = _open(args, want_names=False)
    if args.png:
        emu.screenshot(args.png)
        print(f"wrote {args.png}")
    elif args.raw:
        tm = emu.tilemap()
        for y in range(18):
            print(" ".join("%02x" % b for b in tm[y * 20:(y + 1) * 20]))
    else:
        print("\n".join(emu.screen_text()))
    emu.stop()


def cmd_state(args):
    emu, names, _ = _open(args)
    print(json.dumps(game_state(emu, names, include_screen=args.screen),
                     indent=2, ensure_ascii=False))
    emu.stop()


def cmd_read(args):
    emu, _, _ = _open(args, want_names=False)
    try:
        data = emu.read(args.symbol if not args.symbol.startswith("0x")
                        else int(args.symbol, 16), args.length)
    except KeyError:
        sys.exit(f"unknown symbol {args.symbol!r}; try `crystal sym {args.symbol}`")
    if args.text:
        print(emu.charmap.decode(data))
    else:
        print(data.hex(" "))
    emu.stop()


def cmd_sym(args):
    from .symfile import Symbols
    for name, bank, addr in Symbols(paths.SYM).find(args.pattern)[:args.limit]:
        print(f"{bank:02x}:{addr:04x} {name}")


def _copy_state(src, dst):
    shutil.copyfile(src, dst)
    meta = Path(f"{src}.meta")
    if meta.exists():
        shutil.copyfile(meta, f"{dst}.meta")


def cmd_save(args):
    _copy_state(args.state, args.path)
    print(f"saved {args.state} -> {args.path}")


def cmd_load(args):
    _copy_state(args.path, args.state)
    print(f"loaded {args.path} -> {args.state}")


def main():
    p = argparse.ArgumentParser(
        prog="crystal", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--state", default=str(paths.DEFAULT_STATE),
                   help="savestate file holding the machine (default: %(default)s)")
    p.add_argument("--quiet", "-q", action="store_true",
                   help="suppress the screen dump after mutating commands")
    sub = p.add_subparsers(dest="cmd", required=True)

    q = sub.add_parser("boot", help="power on and create the state file")
    q.add_argument("--frames", type=int, default=60)
    q.add_argument("--force", action="store_true")
    _quiet_opt(q)
    q.set_defaults(fn=cmd_boot)

    q = sub.add_parser("run", help="advance N frames with no input")
    q.add_argument("frames", type=int)
    _quiet_opt(q)
    q.set_defaults(fn=cmd_run)

    q = sub.add_parser("input", help="run an input sequence, e.g. 'A .:30 UP:16 A+B:5 A:2*10'")
    q.add_argument("sequence", nargs="+")
    q.add_argument("--until", help="repeat the sequence until this text is on screen")
    q.add_argument("--max-frames", type=int, default=6000)
    _quiet_opt(q)
    q.set_defaults(fn=cmd_input)

    q = sub.add_parser("saves", help="list savestate checkpoints")
    _quiet_opt(q)
    q.set_defaults(fn=cmd_saves)

    q = sub.add_parser("mash", help="press a button repeatedly until text appears")
    q.add_argument("button", nargs="?", default="A")
    q.add_argument("--until", help="stop when this text is on screen")
    q.add_argument("--every", type=int, default=10, help="frames between presses")
    q.add_argument("--max-frames", type=int, default=2000)
    _quiet_opt(q)
    q.set_defaults(fn=cmd_mash)

    q = sub.add_parser("screen", help="print the decoded 20x18 text screen")
    q.add_argument("--raw", action="store_true", help="hex tile ids instead of text")
    q.add_argument("--png", help="write a pixel screenshot instead")
    _quiet_opt(q)
    q.set_defaults(fn=cmd_screen)

    q = sub.add_parser("state", help="structured game state as JSON")
    q.add_argument("--screen", action="store_true", help="include the text screen")
    _quiet_opt(q)
    q.set_defaults(fn=cmd_state)

    q = sub.add_parser("read", help="read bytes at a symbol (or 0xADDR)")
    q.add_argument("symbol")
    q.add_argument("--length", "-n", type=int, default=1)
    q.add_argument("--text", action="store_true", help="decode via charmap")
    _quiet_opt(q)
    q.set_defaults(fn=cmd_read)

    q = sub.add_parser("sym", help="search the symbol table")
    q.add_argument("pattern")
    q.add_argument("--limit", type=int, default=40)
    _quiet_opt(q)
    q.set_defaults(fn=cmd_sym)

    q = sub.add_parser("save", help="copy the current state file to PATH")
    q.add_argument("path")
    _quiet_opt(q)
    q.set_defaults(fn=cmd_save)

    q = sub.add_parser("load", help="copy PATH over the current state file")
    q.add_argument("path")
    _quiet_opt(q)
    q.set_defaults(fn=cmd_load)

    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
