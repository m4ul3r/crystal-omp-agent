"""Signature-validated PyBoy event hooks (pattern from ai-plays-pokemon's
rom_hooks, addresses resolved from OUR disassembly sym file).

Each entry pins a PC anchor to the first opcode bytes of this build's ROM;
the ROM hash stamped in every .meta sidecar is what makes those bytes
meaningful. install() verifies EVERY signature before registering anything,
and degrades to no-hooks (callers fall back to their polling heuristics)
on any mismatch -- a rebuilt ROM must never silently mis-fire events.

Anchors (pokecrystal disassembly, this build):
  page_wait      00:0AAF  PromptButton     home/joypad.asm:383 -- a finished
                            text page blinks its cursor and waits for A/B
  menu_2d        00:202A  _2DMenu          home/menu.asm -- 2D menus:
                            yes/no boxes, the battle FIGHT/PKMN/PACK/RUN grid
  menu_open      00:1C10  InitVerticalMenuCursor
                            engine/menus/menu.asm -- every vertical menu open:
                            START menu, yes/no boxes, party/pack lists
  battle_end     0F:769E  ExitBattle       engine/battle/core.asm -- battle
                            teardown entered"""

import logging
import os
from collections import deque

from crystalagent import paths

HOOKS = {
    "page_wait": (0x00, 0x0AAF, bytes.fromhex("fadcc2a7200c")),
    "menu_2d": (0x00, 0x202A, bytes.fromhex("f09dea94cf3e")),
    "menu_open": (0x00, 0x1C10, bytes.fromhex("216d443e09cf")),
    "battle_end": (0x0F, 0x769E, bytes.fromhex("cda576cdd076")),
}

_STOP_EVENTS = {"menu_2d", "menu_open", "battle_end"}


def _rom_offset(bank, addr):
    return bank * 0x4000 + (addr - 0x4000 if addr >= 0x4000 else addr)


def enabled():
    """Env kill-switch: CRYSTAL_HOOKS=0 forces the polling heuristics."""
    return os.environ.get("CRYSTAL_HOOKS", "1").strip().lower() not in (
        "0", "no", "false")


class HookEvents:
    def __init__(self):
        self.events = deque(maxlen=256)
        self.live = set()

    def _make_cb(self, name):
        def cb(context):
            self.events.append((name, context.frame_count))
        return cb

    def has(self, *names):
        return any(n in self.live for n in names)

    def drain(self):
        out, self.events = list(self.events), deque(maxlen=256)
        return out

    def saw_since(self, frame, names):
        return any(n in names and fr >= frame for n, fr in self.events)

    def report(self):
        return sorted(self.live)


def install(emu):
    """Validate signatures transactionally, then register. Returns a live
    HookEvents or None (mismatch / disabled), never raises."""
    if not enabled():
        logging.getLogger("trek").info("[hooks] disabled by CRYSTAL_HOOKS=0")
        return None
    rom = open(paths.ROM, "rb").read()
    bad = []
    for name, (bank, addr, expected) in HOOKS.items():
        actual = rom[_rom_offset(bank, addr):
                     _rom_offset(bank, addr) + len(expected)]
        if actual != expected:
            bad.append(f"{name} {bank:02x}:{addr:04X} "
                       f"expected={expected.hex()} actual={actual.hex()}")
    if bad:
        logging.getLogger("trek").warning(
            "[hooks] signature mismatch (rebuilt ROM? falling back to "
            "polling heuristics): %s", "; ".join(bad))
        return None
    hooks = HookEvents()
    pyboy = emu.py
    for name, (bank, addr, _) in HOOKS.items():
        pyboy.hook_register(bank, addr, hooks._make_cb(name), pyboy)
        hooks.live.add(name)
    logging.getLogger("trek").info(
        "[hooks] live: %s", ", ".join(hooks.report()))
    return hooks
