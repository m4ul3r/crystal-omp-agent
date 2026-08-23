#!/usr/bin/env python3
"""Release all keys, verify the game sees a clean joypad, optionally save."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from trek import Driver
from pyboy.utils import WindowEvent


def main():
    d = Driver()
    e = d.emu
    for b in ("up", "down", "left", "right", "a", "b", "start", "select"):
        e.py.button_release(b)
        e.py.button_press(b)
        e.py.button_release(b)
    e.tick(20)
    joy = e.read_u8("hJoyDown")
    print(f"hJoyDown={joy:#04x} pos={d.pos()} map={d.map_name()} frame={e.frame}")
    if len(sys.argv) > 1 and sys.argv[1] == "--save":
        d.emu.save(d.state_path)
        print("saved")


if __name__ == "__main__":
    main()
